from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
import json
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import APP_DISPLAY_NAME, APP_SLUG
from app.database import AssetSnapshot, Bill, ImportArtifact, ImportBatch, LedgerOrigin, ReviewCandidate, SessionLocal, TagAudit, init_db
from app.file_import import normalise_rows, parse_upload, preview_rows, suggest_mapping
from app.schemas import AssetCreate, AssetRead, BillCreate, BillRead, CandidateDecision, ImportBatchRead, ImportPreviewRead, ReviewCandidateRead, TagApply, TagAuditRead, TagRequest, TagResult
from app.tagging import classify

APP_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title=APP_DISPLAY_NAME, version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


STRATEGY_CONFIDENCE = {
    "local_rules": 0.45,
    "llm_suggestion": 0.70,
    "manual": 0.95,
    "authorised_auto": 1.0,
}


def bill_read(db: Session, bill: Bill) -> BillRead:
    origin = db.scalar(select(LedgerOrigin).where(LedgerOrigin.bill_id == bill.id))
    return BillRead(
        id=bill.id,
        occurred_at=bill.occurred_at,
        merchant=bill.merchant,
        note=bill.note,
        amount=bill.amount,
        category=bill.category,
        tags=[tag for tag in bill.tags.split(",") if tag],
        source_type=origin.source_type if origin else None,
        source_reference=origin.source_reference if origin else None,
        import_batch_id=origin.import_batch_id if origin else None,
    )


def _apply_tag(db: Session, bill: Bill, strategy: str, category: str, tags: list[str], provider: str, confidence: float) -> TagAudit:
    current = db.scalar(select(TagAudit).where(TagAudit.bill_id == bill.id, TagAudit.superseded.is_(False)).order_by(TagAudit.confidence.desc(), TagAudit.id.desc()))
    superseded = bool(current and confidence < current.confidence)
    audit = TagAudit(bill_id=bill.id, category=category, tags=",".join(tags), strategy=strategy, confidence=confidence, provider=provider, superseded=superseded, created_at=datetime.now())
    if not superseded:
        if current:
            current.superseded = True
        bill.category = category
        bill.tags = ",".join(tags)
    db.add(audit)
    return audit


def _generate_candidates(db: Session, bill: Bill) -> int:
    created = 0
    prior_bills = db.scalars(select(Bill).where(Bill.id != bill.id)).all()
    for other in prior_bills:
        seconds_apart = abs((bill.occurred_at - other.occurred_at).total_seconds())
        if seconds_apart > 300 or abs(abs(bill.amount) - abs(other.amount)) > 0.01:
            continue
        if bill.amount == other.amount and bill.merchant == other.merchant:
            candidate_type, confidence, reason = "duplicate", 0.92, "金额、交易方和 5 分钟内交易时间一致"
        elif bill.amount * other.amount < 0:
            candidate_type, confidence, reason = "transfer", 0.78, "同额反向流水，可能是账户间资产转移"
        else:
            continue
        db.add(ReviewCandidate(candidate_type=candidate_type, bill_id=bill.id, related_bill_id=other.id, confidence=confidence, reason=reason, status="pending", created_at=datetime.now()))
        created += 1
    return created


def _validate_source_type(source_type: str) -> None:
    if source_type not in {"alipay", "wechat"}:
        raise HTTPException(status_code=404, detail="Only alipay and wechat import adapters are enabled")


def _mapping_from_header(value: str | None, columns: list[str]) -> dict[str, str | None]:
    mapping = suggest_mapping(columns)
    if not value:
        return mapping
    try:
        requested = json.loads(value)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=422, detail="Import mapping must be valid JSON") from error
    if not isinstance(requested, dict) or any(field not in mapping for field in requested):
        raise HTTPException(status_code=422, detail="Import mapping has unsupported fields")
    for field, column in requested.items():
        if column is not None and (not isinstance(column, str) or column not in columns):
            raise HTTPException(status_code=422, detail="Import mapping references an unavailable column")
        mapping[field] = column
    return mapping


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"app_name": APP_DISPLAY_NAME})


@app.get("/api/health")
def health():
    return {"status": "ok", "service": APP_SLUG}


@app.post("/api/tag", response_model=TagResult)
def tag_bill(payload: TagRequest):
    category, tags, provider = classify(payload.merchant, payload.note)
    return TagResult(category=category, tags=tags, provider=provider)


@app.get("/api/bills", response_model=list[BillRead])
def list_bills(db: Session = Depends(get_db)):
    return [bill_read(db, bill) for bill in db.scalars(select(Bill).order_by(Bill.occurred_at.desc())).all()]


@app.post("/api/bills", response_model=BillRead, status_code=201)
def create_bill(payload: BillCreate, db: Session = Depends(get_db)):
    category, tags, provider = classify(payload.merchant, payload.note)
    bill = Bill(**payload.model_dump(), category=category, tags=",".join(tags))
    db.add(bill)
    db.flush()
    _apply_tag(db, bill, "local_rules", category, tags, provider, STRATEGY_CONFIDENCE["local_rules"])
    _generate_candidates(db, bill)
    db.commit()
    db.refresh(bill)
    return bill_read(db, bill)


@app.post("/api/imports/{source_type}/preview", response_model=ImportPreviewRead)
async def preview_import(
    source_type: str,
    request: Request,
    filename: str = "import.csv",
    import_password: str | None = Header(default=None, alias="X-Import-Password"),
):
    _validate_source_type(source_type)
    try:
        parsed = parse_upload(await request.body(), filename, import_password)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ImportPreviewRead(
        source_type=source_type,
        filename=parsed.filename,
        file_format=parsed.file_format,
        archive_entry=parsed.archive_entry,
        file_sha256=parsed.file_sha256,
        row_count=len(parsed.rows),
        columns=parsed.columns,
        mapping=suggest_mapping(parsed.columns),
        preview_rows=preview_rows(parsed),
    )


@app.post("/api/imports/{source_type}", response_model=ImportBatchRead, status_code=201)
async def commit_import(
    source_type: str,
    request: Request,
    filename: str = "import.csv",
    import_password: str | None = Header(default=None, alias="X-Import-Password"),
    import_mapping: str | None = Header(default=None, alias="X-Import-Mapping"),
    db: Session = Depends(get_db),
):
    _validate_source_type(source_type)
    try:
        parsed = parse_upload(await request.body(), filename, import_password)
        mapping = _mapping_from_header(import_mapping, parsed.columns)
        imported_rows = normalise_rows(parsed, mapping)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    duplicate = db.scalar(select(ImportArtifact).where(ImportArtifact.source_type == source_type, ImportArtifact.sha256 == parsed.file_sha256))
    if duplicate:
        raise HTTPException(status_code=409, detail="This source file was already imported; duplicate import was blocked")
    batch = ImportBatch(source_type=source_type, filename=parsed.filename, imported_at=datetime.now(), row_count=len(imported_rows), imported_count=0)
    db.add(batch)
    db.flush()
    db.add(ImportArtifact(import_batch_id=batch.id, source_type=source_type, filename=parsed.filename, file_format=parsed.file_format, archive_entry=parsed.archive_entry, sha256=parsed.file_sha256))
    candidate_count = 0
    for row in imported_rows:
        bill = Bill(occurred_at=row.occurred_at, merchant=row.merchant, note=row.note, amount=row.amount, category="未分类", tags="")
        db.add(bill)
        db.flush()
        db.add(LedgerOrigin(bill_id=bill.id, source_type=source_type, source_reference=row.reference, raw_payload=row.raw_payload, import_batch_id=batch.id))
        category, tags, provider = classify(row.merchant, row.note)
        _apply_tag(db, bill, "local_rules", category, tags, provider, STRATEGY_CONFIDENCE["local_rules"])
        candidate_count += _generate_candidates(db, bill)
        batch.imported_count += 1
    db.commit()
    return ImportBatchRead(id=batch.id, source_type=batch.source_type, filename=batch.filename, imported_at=batch.imported_at, row_count=batch.row_count, imported_count=batch.imported_count, candidate_count=candidate_count, file_sha256=parsed.file_sha256, file_format=parsed.file_format, archive_entry=parsed.archive_entry)


@app.post("/api/bills/{bill_id}/tags", response_model=TagAuditRead, status_code=201)
def apply_tag(bill_id: int, payload: TagApply, db: Session = Depends(get_db)):
    bill = db.get(Bill, bill_id)
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    suggested_category, suggested_tags, provider = classify(bill.merchant, bill.note)
    category = payload.category or suggested_category
    tags = payload.tags if payload.tags is not None else suggested_tags
    if payload.strategy == "manual" and not payload.tags:
        raise HTTPException(status_code=422, detail="Manual tagging requires at least one tag")
    confidence = payload.confidence if payload.confidence is not None else STRATEGY_CONFIDENCE[payload.strategy]
    audit = _apply_tag(db, bill, payload.strategy, category, tags, provider, confidence)
    db.commit()
    db.refresh(audit)
    return TagAuditRead(id=audit.id, category=audit.category, tags=[tag for tag in audit.tags.split(",") if tag], strategy=audit.strategy, confidence=audit.confidence, provider=audit.provider, superseded=audit.superseded, created_at=audit.created_at)


@app.get("/api/bills/{bill_id}/tags", response_model=list[TagAuditRead])
def list_tag_audits(bill_id: int, db: Session = Depends(get_db)):
    if not db.get(Bill, bill_id):
        raise HTTPException(status_code=404, detail="Bill not found")
    audits = db.scalars(select(TagAudit).where(TagAudit.bill_id == bill_id).order_by(TagAudit.created_at.desc(), TagAudit.id.desc())).all()
    return [TagAuditRead(id=audit.id, category=audit.category, tags=[tag for tag in audit.tags.split(",") if tag], strategy=audit.strategy, confidence=audit.confidence, provider=audit.provider, superseded=audit.superseded, created_at=audit.created_at) for audit in audits]


@app.get("/api/candidates", response_model=list[ReviewCandidateRead])
def list_candidates(db: Session = Depends(get_db)):
    candidates = db.scalars(select(ReviewCandidate).order_by(ReviewCandidate.created_at.desc())).all()
    return [ReviewCandidateRead(id=item.id, candidate_type=item.candidate_type, confidence=item.confidence, reason=item.reason, status=item.status, created_at=item.created_at, bill=bill_read(db, db.get(Bill, item.bill_id)), related_bill=bill_read(db, db.get(Bill, item.related_bill_id))) for item in candidates]


@app.post("/api/candidates/{candidate_id}", response_model=ReviewCandidateRead)
def decide_candidate(candidate_id: int, payload: CandidateDecision, db: Session = Depends(get_db)):
    candidate = db.get(ReviewCandidate, candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    candidate.status = payload.status
    db.commit()
    db.refresh(candidate)
    return ReviewCandidateRead(id=candidate.id, candidate_type=candidate.candidate_type, confidence=candidate.confidence, reason=candidate.reason, status=candidate.status, created_at=candidate.created_at, bill=bill_read(db, db.get(Bill, candidate.bill_id)), related_bill=bill_read(db, db.get(Bill, candidate.related_bill_id)))


@app.delete("/api/bills/{bill_id}", status_code=204)
def delete_bill(bill_id: int, db: Session = Depends(get_db)):
    bill = db.get(Bill, bill_id)
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    db.delete(bill)
    db.commit()


@app.get("/api/assets", response_model=list[AssetRead])
def list_assets(db: Session = Depends(get_db)):
    return [AssetRead(id=asset.id, account_name=asset.account_name, account_type=asset.account_type, balance=asset.balance, recorded_at=asset.recorded_at) for asset in db.scalars(select(AssetSnapshot).order_by(AssetSnapshot.recorded_at.desc())).all()]


@app.post("/api/assets", response_model=AssetRead, status_code=201)
def create_asset(payload: AssetCreate, db: Session = Depends(get_db)):
    asset = AssetSnapshot(**payload.model_dump())
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return AssetRead(id=asset.id, account_name=asset.account_name, account_type=asset.account_type, balance=asset.balance, recorded_at=asset.recorded_at)


@app.get("/api/dashboard")
def dashboard(db: Session = Depends(get_db)):
    confirmed_transfers = db.scalars(select(ReviewCandidate).where(ReviewCandidate.candidate_type == "transfer", ReviewCandidate.status == "confirmed")).all()
    transfer_bill_ids = {bill_id for candidate in confirmed_transfers for bill_id in (candidate.bill_id, candidate.related_bill_id)}
    ledger_filter = ~Bill.id.in_(transfer_bill_ids) if transfer_bill_ids else True
    income = db.scalar(select(func.coalesce(func.sum(Bill.amount), 0.0)).where(ledger_filter, Bill.amount > 0))
    spending = db.scalar(select(func.coalesce(func.sum(Bill.amount), 0.0)).where(ledger_filter, Bill.amount < 0))
    return {"income": income, "spending": spending, "net": income + spending, "bill_count": db.scalar(select(func.count(Bill.id))), "candidate_count": db.scalar(select(func.count(ReviewCandidate.id)).where(ReviewCandidate.status == "pending")), "generated_at": datetime.now().isoformat()}
