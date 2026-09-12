from __future__ import annotations

import base64
import json
import re
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import MetaData, Table, func, inspect, select, text
from sqlalchemy.exc import OperationalError, IntegrityError
from sqlalchemy.orm import Session

from app import database
from app.api.controllers.dashboard import router as dashboard_router
from app.api.controllers.import_issue import router as import_issue_router
from app.api.controllers.intake import router as intake_router
from app.api.controllers.target_intake import router as target_intake_router
from app.api.controllers.ledger import router as ledger_router
from app.api.controllers.matter import router as matter_router
from app.api.controllers.refund import router as refund_router
from app.api.controllers.review import router as review_router
from app.api.controllers.tag import router as tag_router
from app.api.controllers.tag_view import router as tag_view_router
from app.api.controllers.target_ledger import router as target_ledger_router, v1_router as target_ledger_v1_router
from app.api.controllers.target_review import router as target_review_router
from app.api.controllers.target_tag import router as target_tag_router
from app.api.deps import get_db
from app.config import APP_DISPLAY_NAME, APP_SLUG
from app.database import AccountRevision, AssetSnapshot, Bill, BillViewTag, CandidateActionLog, ImportArtifact, ImportBatch, LedgerOrigin, RefundAllocation, ReviewCandidate, SessionLocal, TagAudit, TagView, ViewTag, init_db
from app.file_import import normalise_rows, parse_upload, preview_rows, inspect_rows
from app.schemas import AccountRevisionRequest, AssetCreate, AssetRead, BatchImportItemRead, BatchImportRead, BatchImportRequest, BatchPreviewItemRead, BatchPreviewRead, BillCreate, BillRead, CandidateDecision, ImportBatchRead, ImportPreviewRead, ReviewCandidateRead, TagApply, TagAuditRead, TagRequest, TagResult, TagStateAssignmentRequest, UndoRequest, ViewTagAssignmentRead, ViewTagAssignmentRequest
from app.tagging import classify, classify_rules
from app.services.review_service import ReviewService
from app.services.candidate_suggestion_service import CandidateSuggestionService
from app.database import RefundDesignation, ImportRowIssue
from app.money import cents, money
from app.review_matters import assert_no_matters

APP_DIR = Path(__file__).parent
from app.database import ImportEvidence, ImportIdentity, Account


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    # Legacy regression runtime only: resolve the factory dynamically so its
    # initializer and startup query cannot target different databases.
    with database.SessionLocal() as db:
        _consolidate_duplicate_candidates(db)
        db.commit()
    yield


app = FastAPI(title=APP_DISPLAY_NAME, version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
app.include_router(dashboard_router)
app.include_router(import_issue_router)
app.include_router(intake_router)
app.include_router(target_intake_router)
app.include_router(ledger_router)
app.include_router(matter_router)
app.include_router(refund_router)
app.include_router(review_router)
app.include_router(tag_router)
app.include_router(tag_view_router)
app.include_router(target_ledger_router)
app.include_router(target_ledger_v1_router)
app.include_router(target_review_router)
app.include_router(target_tag_router)
templates = Jinja2Templates(directory=APP_DIR / "templates")


STRATEGY_CONFIDENCE = {
    "local_rules": 0.45,
    "llm_suggestion": 0.70,
    "manual": 0.95,
    "authorised_auto": 1.0,
}

TAG_STATE_MAX_LENGTH = 2048
SYSTEM_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
CATEGORY_SYSTEM_NAME_ALIASES = {
    "餐饮": "food",
    "交通出行": "transport",
    "居住": "lodging",
    "购物": "shopping",
}


# This is deliberately a fixed application schema allow-list.  The database
# observer never accepts a SQL expression or an arbitrary SQLite object name.
DATABASE_OBSERVER_TABLES = frozenset({
    "review_matters", "review_matter_revisions", "refund_designations", "refund_nature_audits", "import_row_issues", "import_issue_actions",
    "asset_snapshots",
    "account_revisions",
    "bill_tags",
    "bill_view_tags",
    "bills",
    "candidate_action_logs",
    "import_artifacts",
    "import_batches",
    "ledger_origins",
    "review_candidates",
    "refund_allocation_audits",
    "refund_allocations",
    "tag_audits",
    "tag_change_logs",
    "tag_views",
    "tags",
    "view_tags",
})
DATABASE_OBSERVER_CELL_LIMIT = 600


def _database_observer_table_names(db: Session) -> list[str]:
    inspector = inspect(db.bind)
    return sorted(name for name in inspector.get_table_names() if name in DATABASE_OBSERVER_TABLES)


def _database_observer_table(db: Session, table_name: str) -> Table:
    if table_name not in _database_observer_table_names(db):
        raise HTTPException(status_code=404, detail="Unknown read-only database table")
    return Table(table_name, MetaData(), autoload_with=db.bind)


def _database_value(value: object) -> object:
    """Return a display-safe value without persisting or logging a second copy."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (datetime, date)):
        value = value.isoformat()
    elif isinstance(value, bytes):
        value = f"<{len(value)} bytes>"
    else:
        value = str(value)
    return value if len(value) <= DATABASE_OBSERVER_CELL_LIMIT else f"{value[:DATABASE_OBSERVER_CELL_LIMIT]}… [truncated]"


def _database_metadata(db: Session, table_name: str) -> dict:
    inspector = inspect(db.bind)
    primary_key = set((inspector.get_pk_constraint(table_name) or {}).get("constrained_columns") or [])
    return {
        "columns": [
            {
                "name": column["name"],
                "type": str(column["type"]),
                "nullable": bool(column.get("nullable", True)),
                "primary_key": column["name"] in primary_key,
            }
            for column in inspector.get_columns(table_name)
        ],
        "indexes": [
            {"name": index["name"], "columns": index.get("column_names", []), "unique": bool(index.get("unique", False))}
            for index in inspector.get_indexes(table_name)
        ],
    }


def _system_name_or_422(value: str, noun: str) -> str:
    if not SYSTEM_NAME_PATTERN.fullmatch(value):
        raise HTTPException(status_code=422, detail=f"{noun} system_name must use lowercase letters, numbers, and underscores")
    return value


def _active_tag_views(db: Session) -> list[TagView]:
    return db.scalars(select(TagView).where(TagView.archived.is_(False)).order_by(TagView.id)).all()


def _state_from_bill(bill: Bill) -> dict[str, str]:
    try:
        state = json.loads(bill.tag_state_json or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return state if isinstance(state, dict) else {}


def _validate_tag_state(db: Session, submitted: dict[str, str]) -> dict[str, str]:
    if not isinstance(submitted, dict):
        raise HTTPException(status_code=422, detail="tag_state must be a JSON object")
    try:
        submitted_json = json.dumps(submitted, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail="tag_state must be JSON serializable") from error
    if len(submitted_json) > TAG_STATE_MAX_LENGTH:
        raise HTTPException(status_code=422, detail=f"tag_state must be at most {TAG_STATE_MAX_LENGTH} bytes")
    views = {view.system_name: view for view in _active_tag_views(db)}
    state: dict[str, str] = {}
    for view_system_name, tag_system_name in submitted.items():
        if not isinstance(view_system_name, str) or not isinstance(tag_system_name, str):
            raise HTTPException(status_code=422, detail="tag_state keys and values must be system-name strings")
        _system_name_or_422(view_system_name, "Tag view")
        _system_name_or_422(tag_system_name, "Tag")
        view = views.get(view_system_name)
        if not view:
            raise HTTPException(status_code=422, detail=f"Unknown or archived tag view: {view_system_name}")
        tag = db.scalar(select(ViewTag).where(
            ViewTag.view_id == view.id,
            ViewTag.system_name == tag_system_name,
            ViewTag.archived.is_(False),
        ))
        if not tag:
            raise HTTPException(status_code=422, detail=f"Unknown or archived tag in {view_system_name}: {tag_system_name}")
        state[view_system_name] = tag_system_name
    for view_system_name, view in views.items():
        if view_system_name in state:
            continue
        unclassified = db.scalar(select(ViewTag.system_name).where(
            ViewTag.view_id == view.id,
            ViewTag.is_unclassified.is_(True),
        ))
        if not unclassified:
            raise HTTPException(status_code=500, detail=f"Tag view {view_system_name} has no unclassified tag")
        state[view_system_name] = unclassified
    encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded) > TAG_STATE_MAX_LENGTH:
        raise HTTPException(status_code=422, detail=f"tag_state must be at most {TAG_STATE_MAX_LENGTH} bytes")
    return state


def _tag_state_json(state: dict[str, str]) -> str:
    return json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _state_assignments(db: Session, bill: Bill) -> list[ViewTagAssignmentRead]:
    state = _state_from_bill(bill)
    result: list[ViewTagAssignmentRead] = []
    for view in _active_tag_views(db):
        tag_system_name = state.get(view.system_name, "unclassified")
        tag = db.scalar(select(ViewTag).where(ViewTag.view_id == view.id, ViewTag.system_name == tag_system_name))
        if not tag:
            tag = db.scalar(select(ViewTag).where(ViewTag.view_id == view.id, ViewTag.is_unclassified.is_(True)))
        if tag:
            result.append(ViewTagAssignmentRead(
                view_id=view.id,
                view_name=view.name,
                view_system_name=view.system_name,
                tag_id=tag.id,
                tag_name=tag.name,
                tag_system_name=tag.system_name,
                strategy="tag_state",
                confidence=0.95,
            ))
    return result


def _write_tag_state(
    db: Session,
    bill: Bill,
    submitted: dict[str, str],
    strategy: str,
    confidence: float,
    provider: str,
    reason: str = "",
    idempotency_key: str | None = None,
    request_payload: str = "",
) -> TagAudit:
    before_state = _state_from_bill(bill)
    if not before_state:
        before_state = _validate_tag_state(db, {})
        bill.tag_state_json = _tag_state_json(before_state)
    state = _validate_tag_state(db, submitted)
    current_audits = db.scalars(select(TagAudit).where(
        TagAudit.bill_id == bill.id,
        TagAudit.superseded.is_(False),
    ).order_by(TagAudit.id.desc())).all()
    # Confidence ranks suggestions, never overrides a human decision.
    suggestion = strategy in {"local_rules", "llm_suggestion"}
    superseded = suggestion
    selected = _state_assignments_for_state(db, state)
    audit = TagAudit(
        bill_id=bill.id,
        category=next((tag.tag_name for tag in selected if tag.view_system_name == "category"), bill.category),
        tags=",".join(tag.tag_name for tag in selected),
        tag_state_json=_tag_state_json(state),
        strategy=strategy,
        confidence=confidence,
        provider=provider,
        superseded=superseded,
        action="suggest" if suggestion else "confirm",
        actor="local-user",
        reason=reason,
        before_state_json=_tag_state_json(before_state),
        before_category=bill.category,
        idempotency_key=idempotency_key,
        request_payload=request_payload,
        created_at=datetime.now(),
    )
    if not superseded:
        for current in current_audits:
            current.superseded = True
        bill.tag_state_json = audit.tag_state_json
        bill.category = audit.category
    db.add(audit)
    return audit


def _state_assignments_for_state(db: Session, state: dict[str, str]) -> list[ViewTagAssignmentRead]:
    rows: list[ViewTagAssignmentRead] = []
    for view in _active_tag_views(db):
        tag = db.scalar(select(ViewTag).where(
            ViewTag.view_id == view.id,
            ViewTag.system_name == state.get(view.system_name, "unclassified"),
        ))
        if tag:
            rows.append(ViewTagAssignmentRead(
                view_id=view.id, view_name=view.name, view_system_name=view.system_name,
                tag_id=tag.id, tag_name=tag.name, tag_system_name=tag.system_name,
                strategy="tag_state", confidence=0.95,
            ))
    return rows


def bill_read(db: Session, bill: Bill) -> BillRead:
    origin = db.scalar(select(LedgerOrigin).where(LedgerOrigin.bill_id == bill.id))
    assignments = _state_assignments(db, bill)
    state = _state_from_bill(bill)
    return BillRead(
        id=bill.id,
        occurred_at=bill.occurred_at,
        merchant=bill.merchant,
        note=bill.note,
        amount=bill.amount,
        currency=bill.currency,
        category=bill.category,
        tags=[tag.tag_name for tag in assignments],
        source_type=origin.source_type if origin else None,
        source_reference=origin.source_reference if origin else None,
        import_batch_id=origin.import_batch_id if origin else None,
        account_name=bill.account_name,
        account_id=bill.account_id,
        time_precision=bill.time_precision,
        import_nature=bill.import_nature,
        direction="收入" if bill.amount >= 0 else "支出",
        aggregate_excluded=bill.aggregate_excluded,
        transfer_group_id=bill.transfer_group_id,
        duplicate_of_id=bill.duplicate_of_id,
        view_tags=assignments,
        tag_state=state,
        tag_revision_id=db.scalar(select(TagAudit.id).where(TagAudit.bill_id == bill.id, TagAudit.superseded.is_(False)).order_by(TagAudit.id.desc()).limit(1)) or 0,
    )


def _bill_view_tags(db: Session, bill_id: int) -> list[ViewTagAssignmentRead]:
    bill = db.get(Bill, bill_id)
    return _state_assignments(db, bill) if bill else []


def _normalise_tags(tags: list[str]) -> list[str]:
    return list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))


def _apply_tag(db: Session, bill: Bill, strategy: str, category: str, tags: list[str], provider: str, confidence: float, reason: str = "", idempotency_key: str | None = None, request_payload: str = "") -> TagAudit:
    category_view = db.scalar(select(TagView).where(TagView.system_name == "category", TagView.archived.is_(False)))
    state = _state_from_bill(bill)
    if category_view:
        category_tag = db.scalar(select(ViewTag).where(
            ViewTag.view_id == category_view.id,
            ViewTag.system_name == CATEGORY_SYSTEM_NAME_ALIASES.get(category, "unclassified"),
            ViewTag.archived.is_(False),
        )) or db.scalar(select(ViewTag).where(
            ViewTag.view_id == category_view.id,
            ViewTag.name == category,
            ViewTag.archived.is_(False),
        ))
        state[category_view.system_name] = category_tag.system_name if category_tag else "unclassified"
    audit = _write_tag_state(db, bill, state, strategy, confidence, provider, reason, idempotency_key, request_payload)
    audit.category = category
    if not audit.superseded:
        bill.category = category
    return audit


def _candidate_member_ids(candidate: ReviewCandidate) -> list[int]:
    try:
        ids = json.loads(candidate.member_bill_ids) if candidate.member_bill_ids else []
    except json.JSONDecodeError:
        ids = []
    return list(dict.fromkeys([*ids, candidate.bill_id, candidate.related_bill_id]))


def _candidate_members(db: Session, candidate: ReviewCandidate) -> list[Bill]:
    members = [db.get(Bill, bill_id) for bill_id in _candidate_member_ids(candidate)]
    return sorted((bill for bill in members if bill), key=lambda bill: (bill.occurred_at, bill.id))


def _canonical_candidate(db: Session, candidate: ReviewCandidate | None) -> ReviewCandidate | None:
    while candidate and candidate.status == "superseded_duplicate_group" and candidate.superseded_by_id:
        candidate = db.get(ReviewCandidate, candidate.superseded_by_id)
    return candidate


def _consolidate_duplicate_candidates(db: Session) -> None:
    ReviewService(db).consolidate_duplicates()


def _has_distinct_account_evidence(first: Bill, second: Bill) -> bool:
    if first.account_id is not None and second.account_id is not None:
        return first.account_id != second.account_id
    unknown_accounts = {"", "未提供账户", "手工未提供账户"}
    return (
        first.account_name not in unknown_accounts
        and second.account_name not in unknown_accounts
        and first.account_name != second.account_name
    )


def _candidate_effect(candidate: ReviewCandidate) -> str:
    effects = {
        "pending": "尚未改变流水或收支汇总。",
        "deferred": "稍后处理；两笔流水仍独立计入收支。",
        "ignored": "已忽略；两笔流水仍独立计入收支。",
        "evidence_insufficient": "账户证据不足，已暂缓；两笔流水仍独立计入收支。",
        "personal_transfer_grouped": "已确认个人账户间转移；两笔保留并追踪资产流向，不计入收入/支出汇总、净额或趋势。手续费等不在本候选两笔内的真实成本仍保留统计。",
        "third_party_transfer_grouped": "已确认他人资产转移/代收代付；两笔原始流水与标签保留并标记为不追踪收支，不计入收入/支出、净额或趋势。",
        "transfer_grouped": "已归入同一转移组；两笔保留但不计入收入/支出汇总。",
        "duplicate_excluded": "已保留指定流水；另一笔保留原始记录但不计入收支汇总。",
        "duplicate_rejected": "已拒绝重复建议；候选组所有原始流水继续计入收入、支出、净额和趋势。",
        "legacy_transfer_excluded": "旧版已按转移排除收支；保留原始流水，但没有可补回的账户证据。",
        "legacy_duplicate_needs_review": "旧版曾标记为已确认，但未保存保留哪一笔；两笔仍独立计入收支。",
    }
    return effects[candidate.status]


def candidate_read(db: Session, candidate: ReviewCandidate) -> ReviewCandidateRead:
    return ReviewCandidateRead(
        id=candidate.id,
        current_action_id=db.scalar(select(CandidateActionLog.id).where(CandidateActionLog.candidate_id == candidate.id).order_by(CandidateActionLog.id.desc()).limit(1)) or 0,
        candidate_type=candidate.candidate_type,
        confidence=candidate.confidence,
        reason=candidate.reason,
        status=candidate.status,
        transfer_group_id=candidate.transfer_group_id,
        transfer_kind=candidate.transfer_kind,
        retained_bill_id=candidate.retained_bill_id,
        resolved_at=candidate.resolved_at,
        undo_available=bool(db.scalar(select(CandidateActionLog.id).where(CandidateActionLog.candidate_id == candidate.id, CandidateActionLog.action != "undo", CandidateActionLog.undone.is_(False)).order_by(CandidateActionLog.id.desc()))),
        aggregation_effect=_candidate_effect(candidate),
        created_at=candidate.created_at,
        member_bills=[bill_read(db, bill) for bill in _candidate_members(db, candidate)],
        bill=bill_read(db, db.get(Bill, candidate.bill_id)),
        related_bill=bill_read(db, db.get(Bill, candidate.related_bill_id)),
    )


def _validate_source_type(source_type: str) -> None:
    if source_type not in {"alipay", "wechat"}:
        raise HTTPException(status_code=404, detail="Only alipay and wechat import adapters are enabled")


def _preview_read(source_type: str, parsed) -> ImportPreviewRead:
    valid, issues = inspect_rows(parsed)
    return ImportPreviewRead(
        source_type=source_type,
        filename=parsed.filename,
        file_format=parsed.file_format,
        archive_entry=parsed.archive_entry,
        file_sha256=parsed.file_sha256,
        row_count=len(parsed.rows),
        columns=["交易时间", "交易方", "金额", "备注", "收支", "流水号"],
        preview_rows=preview_rows(parsed),
        valid_count=len(valid),
        issues=issues,
    )


def _batch_read(batch: ImportBatch, parsed, candidate_count: int, issue_count: int = 0) -> ImportBatchRead:
    return ImportBatchRead(
        id=batch.id,
        source_type=batch.source_type,
        filename=batch.filename,
        imported_at=batch.imported_at,
        row_count=batch.row_count,
        imported_count=batch.imported_count,
        candidate_count=candidate_count,
        issue_count=issue_count,
        file_sha256=parsed.file_sha256,
        file_format=parsed.file_format,
        archive_entry=parsed.archive_entry,
        batch_token=batch.batch_token,
    )


def _commit_parsed(db: Session, source_type: str, parsed, batch_token: str | None = None) -> ImportBatchRead:
    from app.statement_parser import payment_account
    from app.smart_import import identity_keys, dump
    duplicate = db.scalar(select(ImportArtifact).where(ImportArtifact.source_type == source_type, ImportArtifact.sha256 == parsed.file_sha256))
    if duplicate:
        raise ValueError("该来源文件已导入，已跳过重复文件")
    imported_rows, issues = inspect_rows(parsed)
    batch = ImportBatch(source_type=source_type, filename=parsed.filename, imported_at=datetime.now(), row_count=len(parsed.rows), imported_count=0, batch_token=batch_token)
    db.add(batch)
    db.flush()
    db.add(ImportArtifact(import_batch_id=batch.id, source_type=source_type, filename=parsed.filename, file_format=parsed.file_format, archive_entry=parsed.archive_entry, sha256=parsed.file_sha256))
    supplemented_count = 0
    references: dict[str, list[int]] = {}
    for origin in db.scalars(select(LedgerOrigin).where(
        LedgerOrigin.source_type == source_type
    )).all():
        raw = json.loads(origin.raw_payload or "{}")
        reference = next((
            raw[key].strip()
            for key in ("交易订单号", "交易单号", "支付宝交易号", "交易号")
            if raw.get(key)
        ), origin.source_reference)
        if reference:
            references.setdefault(reference, []).append(origin.bill_id)
    for item in db.scalars(select(ImportEvidence).where(
        ImportEvidence.bill_id.is_not(None)
    )).all():
        prior = json.loads(item.record_json)
        if prior["source_type"] == source_type and prior.get("reference"):
            ids = references.setdefault(prior["reference"], [])
            if item.bill_id not in ids:
                ids.append(item.bill_id)

    bill_ids = []
    for source_row_number, row in imported_rows:
        record = {
            "source_type": source_type,
            "profile": "",
            "reference": row.reference,
            "amount_minor": cents(row.amount),
            "balance_minor": None,
            "occurred_at": row.occurred_at.isoformat(),
            "currency": "CNY",
            "merchant": row.merchant,
            "note": row.note,
            "nature": "ordinary",
            "time_precision": "second",
            "disposition": "posted",
            "account": payment_account(source_type, "", row.account_name),
            "raw": json.loads(row.raw_payload),
            "row_number": source_row_number,
        }
        matches = references.get(row.reference, []) if row.reference else []
        if len(matches) > 1:
            raise ValueError("同一交易标识已有多笔流水，请先核验历史重复")
        if matches:
            existing = db.get(Bill, matches[0])
            if (
                cents(existing.amount) != cents(row.amount)
                or existing.occurred_at.date() != row.occurred_at.date()
            ):
                raise ValueError("交易标识相同但日期或金额冲突，不能覆盖已有交易")
            db.add(ImportEvidence(
                bill_id=existing.id,
                import_batch_id=batch.id,
                row_number=source_row_number,
                record_json=dump(record),
                disposition="supplement",
            ))
            supplemented_count += 1
            continue
        bill = Bill(occurred_at=row.occurred_at, merchant=row.merchant, note=row.note, amount=row.amount, account_name=row.account_name, category="未分类", tags="")
        db.add(bill)
        db.flush()
        db.add(LedgerOrigin(bill_id=bill.id, source_type=source_type, source_reference=row.reference, raw_payload=row.raw_payload, import_batch_id=batch.id, source_row_number=source_row_number))
        db.add(ImportEvidence(bill_id=bill.id, import_batch_id=batch.id, row_number=source_row_number, record_json=dump(record), disposition='new'))
        for key in identity_keys(record):
            db.add(ImportIdentity(key=key,bill_id=bill.id))
        if row.reference:
            references[row.reference] = [bill.id]
        category, tags, provider = classify_rules(row.merchant, row.note)
        _apply_tag(db, bill, "local_rules", category, tags, provider, STRATEGY_CONFIDENCE["local_rules"])
        bill_ids.append(bill.id)
        batch.imported_count += 1
    for issue in issues:
        db.add(ImportRowIssue(import_batch_id=batch.id, source_row_number=issue["row_number"], raw_payload=json.dumps(issue["raw_fields"], ensure_ascii=False, sort_keys=True), error=issue["error"]))
    candidate_count = CandidateSuggestionService(db).generate(bill_ids)
    db.commit()
    result = _batch_read(batch, parsed, candidate_count, len(issues))
    result.supplemented_count = supplemented_count
    return result


def _decode_batch_file(encoded: str, filename: str) -> bytes:
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError(f"{filename}: 文件编码无效") from error
    if len(content) > 25 * 1024 * 1024:
        raise ValueError(f"{filename}: 文件超过 25 MB 限制")
    return content


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {"app_name": APP_DISPLAY_NAME})


@app.get("/api/health")
def health():
    return {"status": "ok", "service": APP_SLUG}


@app.get("/api/database/tables")
def list_database_tables(db: Session = Depends(get_db)):
    """Describe application-owned SQLite tables for the local read-only observer."""
    tables = []
    for name in _database_observer_table_names(db):
        table = _database_observer_table(db, name)
        tables.append({
            "name": name,
            "row_count": db.scalar(select(func.count()).select_from(table)) or 0,
            **_database_metadata(db, name),
        })
    return {"tables": tables, "read_only": True, "max_page_size": 100, "cell_output_limit": DATABASE_OBSERVER_CELL_LIMIT}


@app.get("/api/database/tables/{table_name}")
def read_database_table(
    table_name: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Read one allow-listed table using SQLAlchemy-bound pagination only."""
    table = _database_observer_table(db, table_name)
    primary_key = list(table.primary_key.columns)
    stable_order = primary_key or [next(iter(table.columns))]
    total = db.scalar(select(func.count()).select_from(table)) or 0
    statement = select(table).order_by(*[column.asc() for column in stable_order]).offset((page - 1) * page_size).limit(page_size)
    rows = [
        {column.name: _database_value(row[column.name]) for column in table.columns}
        for row in db.execute(statement).mappings()
    ]
    return {
        "table": table_name,
        "read_only": True,
        "page": page,
        "page_size": page_size,
        "total": total,
        "sort": {"columns": [column.name for column in stable_order], "order": "asc"},
        **_database_metadata(db, table_name),
        "rows": rows,
    }


@app.post("/api/tag", response_model=TagResult)
def tag_bill(payload: TagRequest):
    category, tags, provider = classify(payload.merchant, payload.note)
    return TagResult(category=category, tags=tags, provider=provider)


@app.get("/api/transactions/{bill_id}/source")
def transaction_source(bill_id: int, db: Session = Depends(get_db)):
    bill = db.get(Bill, bill_id)
    if not bill:
        raise HTTPException(status_code=404, detail="Transaction not found")
    origin = db.scalar(select(LedgerOrigin).where(LedgerOrigin.bill_id == bill_id))
    if not origin:
        return {"bill_id": bill_id, "origin": None, "artifact": None, "batch": None, "raw_fields": {}}
    batch = db.get(ImportBatch, origin.import_batch_id) if origin.import_batch_id else None
    artifact = db.scalar(select(ImportArtifact).where(ImportArtifact.import_batch_id == origin.import_batch_id)) if origin.import_batch_id else None
    try:
        raw_fields = json.loads(origin.raw_payload or "{}")
    except (json.JSONDecodeError, TypeError):
        raw_fields = {"unparsed": origin.raw_payload}
    evidence = []
    evidence_rows = db.execute(select(
        ImportEvidence.row_number,
        ImportEvidence.record_json,
        ImportEvidence.disposition,
        ImportBatch.filename,
        ImportBatch.source_type,
    ).join(
        ImportBatch,
        ImportEvidence.import_batch_id == ImportBatch.id,
    ).where(
        ImportEvidence.bill_id == bill_id,
    ).order_by(ImportEvidence.id)).mappings().all()
    for item in evidence_rows:
        record = json.loads(item["record_json"])
        evidence.append({
            "filename": item["filename"],
            "source_type": item["source_type"],
            "row_number": item["row_number"],
            "raw_fields": record["raw"],
            "disposition": item["disposition"],
        })
        for key, value in record['raw'].items():
            if not raw_fields.get(key):
                raw_fields[key] = value
    return {
        "bill_id": bill_id,
        "origin": {
            "source_type": origin.source_type,
            "source_reference": origin.source_reference,
            "source_row_number": origin.source_row_number,
            "import_batch_id": origin.import_batch_id,
        },
        "artifact": ({
            "filename": artifact.filename,
            "file_format": artifact.file_format,
            "archive_entry": artifact.archive_entry,
            "sha256": artifact.sha256,
        } if artifact else None),
        "batch": ({
            "id": batch.id,
            "filename": batch.filename,
            "imported_at": batch.imported_at,
        } if batch else None),
        "raw_fields": raw_fields,
        "evidence": evidence,
    }




@app.put("/api/transactions/{bill_id}/tag-assignments/{view_id}", response_model=BillRead)
def assign_view_tag(bill_id: int, view_id: int, payload: ViewTagAssignmentRequest, db: Session = Depends(get_db)):
    _begin_immediate(db)
    bill, view, tag = db.get(Bill, bill_id), db.get(TagView, view_id), db.get(ViewTag, payload.tag_id)
    if not bill or not view or not tag or tag.view_id != view.id:
        raise HTTPException(status_code=404, detail="Transaction, tag view, or tag not found")
    state = _state_from_bill(bill)
    state[view.system_name] = tag.system_name
    _write_tag_state(db, bill, state, payload.strategy, payload.confidence, "legacy_assignment_adapter")
    db.commit()
    return bill_read(db, bill)


@app.put("/api/transactions/{bill_id}/tag-state", response_model=BillRead)
def assign_tag_state(bill_id: int, payload: TagStateAssignmentRequest, db: Session = Depends(get_db)):
    _begin_immediate(db)
    bill = db.get(Bill, bill_id)
    if not bill:
        raise HTTPException(status_code=404, detail="Transaction not found")
    latest = db.scalar(select(TagAudit.id).where(TagAudit.bill_id == bill_id, TagAudit.superseded.is_(False)).order_by(TagAudit.id.desc()).limit(1)) or 0
    encoded = json.dumps({"bill_id": bill_id, **payload.model_dump()}, sort_keys=True, ensure_ascii=False)
    if payload.idempotency_key:
        existing = db.scalar(select(TagAudit).where(TagAudit.idempotency_key == payload.idempotency_key))
        if existing:
            if existing.request_payload != encoded or existing.undone or existing.id != latest:
                raise HTTPException(409, "标签请求已发生变化，请刷新后重试")
            db.commit()
            return bill_read(db, bill)
    if payload.expected_audit_id is not None and latest != payload.expected_audit_id:
        raise HTTPException(409, "标签已被其他操作修改，请重新打开")
    _write_tag_state(db, bill, payload.tag_state, payload.strategy, payload.confidence, "named_tag_state", payload.reason, payload.idempotency_key, encoded)
    db.commit()
    return bill_read(db, bill)


@app.post("/api/bills", response_model=BillRead, status_code=201)
def create_bill(payload: BillCreate, db: Session = Depends(get_db)):
    category, tags, provider = classify_rules(payload.merchant, payload.note)
    bill = Bill(**payload.model_dump(), category="未分类", tags="")
    db.add(bill)
    db.flush()
    _apply_tag(db, bill, "local_rules", category, tags, provider, STRATEGY_CONFIDENCE["local_rules"])
    CandidateSuggestionService(db).generate([bill.id])
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
        parsed = parse_upload(source_type, await request.body(), filename, import_password)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"{filename}: {error}") from error
    return _preview_read(source_type, parsed)


@app.post("/api/imports/{source_type}", response_model=ImportBatchRead, status_code=201)
async def commit_import(
    source_type: str,
    request: Request,
    filename: str = "import.csv",
    import_password: str | None = Header(default=None, alias="X-Import-Password"),
    batch_token: str | None = None,
    db: Session = Depends(get_db),
):
    _validate_source_type(source_type)
    try:
        parsed = parse_upload(source_type, await request.body(), filename, import_password)
        return _commit_parsed(db, source_type, parsed, batch_token)
    except ValueError as error:
        db.rollback()
        status = 409 if "重复文件" in str(error) else 422
        raise HTTPException(status_code=status, detail=f"{filename}: {error}") from error
    except (IntegrityError, OperationalError) as error:
        db.rollback()
        raise HTTPException(409, "文件已被其他请求导入或账本正在写入，请刷新后重试") from error


@app.post("/api/imports/{source_type}/batch/preview", response_model=BatchPreviewRead)
def preview_import_batch(
    source_type: str,
    payload: BatchImportRequest,
    import_password: str | None = Header(default=None, alias="X-Import-Password"),
    db: Session = Depends(get_db),
):
    _validate_source_type(source_type)
    batch_token = payload.batch_token or uuid4().hex
    files: list[BatchPreviewItemRead] = []
    fingerprints: set[str] = set()
    for item in payload.files:
        try:
            parsed = parse_upload(source_type, _decode_batch_file(item.content_base64, item.filename), item.filename, import_password)
            duplicate = parsed.file_sha256 in fingerprints or bool(db.scalar(select(ImportArtifact).where(ImportArtifact.source_type == source_type, ImportArtifact.sha256 == parsed.file_sha256)))
            fingerprints.add(parsed.file_sha256)
            files.append(BatchPreviewItemRead(filename=item.filename, ok=True, duplicate=duplicate, preview=_preview_read(source_type, parsed)))
        except ValueError as error:
            files.append(BatchPreviewItemRead(filename=item.filename, ok=False, error=str(error)))
    return BatchPreviewRead(batch_token=batch_token, files=files)


@app.post("/api/imports/{source_type}/batch", response_model=BatchImportRead, status_code=201)
def commit_import_batch(
    source_type: str,
    payload: BatchImportRequest,
    import_password: str | None = Header(default=None, alias="X-Import-Password"),
    db: Session = Depends(get_db),
):
    _validate_source_type(source_type)
    batch_token = payload.batch_token or uuid4().hex
    files: list[BatchImportItemRead] = []
    for item in payload.files:
        try:
            parsed = parse_upload(source_type, _decode_batch_file(item.content_base64, item.filename), item.filename, import_password)
            committed = _commit_parsed(db, source_type, parsed, batch_token)
            files.append(BatchImportItemRead(filename=item.filename, status="imported", import_batch=committed))
        except ValueError as error:
            db.rollback()
            status = "duplicate" if "重复文件" in str(error) else "error"
            files.append(BatchImportItemRead(filename=item.filename, status=status, error=str(error)))
        except (IntegrityError, OperationalError):
            db.rollback()
            files.append(BatchImportItemRead(filename=item.filename, status="error", error="文件重复或账本写入冲突，请刷新后重试；本文件未部分提交"))
    return BatchImportRead(batch_token=batch_token, files=files)


def _tag_audit_read(audit: TagAudit) -> TagAuditRead:
    try:
        state = json.loads(audit.tag_state_json or "{}")
    except (json.JSONDecodeError, TypeError):
        state = {}
    return TagAuditRead(
        id=audit.id,
        category=audit.category,
        tags=[tag for tag in audit.tags.split(",") if tag],
        strategy=audit.strategy,
        confidence=audit.confidence,
        provider=audit.provider,
        superseded=audit.superseded,
        action=audit.action,
        actor=audit.actor,
        reason=audit.reason,
        reverses_audit_id=audit.reverses_audit_id,
        undone=audit.undone,
        undone_at=audit.undone_at,
        created_at=audit.created_at,
        tag_state=state if isinstance(state, dict) else {},
    )


@app.post("/api/bills/{bill_id}/tags", response_model=TagAuditRead, status_code=201)
def apply_tag(bill_id: int, payload: TagApply, db: Session = Depends(get_db)):
    bill = db.get(Bill, bill_id)
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    request_payload = json.dumps(payload.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if payload.strategy == "manual" and not payload.tags:
        raise HTTPException(status_code=422, detail="Manual tagging requires at least one tag")
    if payload.strategy == "manual":
        category = payload.category or "人工分类"
        tags = payload.tags or []
        provider = "manual"
    elif payload.strategy == "local_rules":
        suggested_category, suggested_tags, provider = classify_rules(bill.merchant, bill.note)
        category = payload.category or suggested_category
        tags = payload.tags if payload.tags is not None else suggested_tags
    else:
        suggested_category, suggested_tags, provider = classify(bill.merchant, bill.note)
        category = payload.category or suggested_category
        tags = payload.tags if payload.tags is not None else suggested_tags
    confidence = payload.confidence if payload.confidence is not None else STRATEGY_CONFIDENCE[payload.strategy]
    # External classification must never hold the ledger write lock.
    db.rollback()
    _begin_immediate(db)
    bill = db.get(Bill, bill_id)
    if payload.idempotency_key:
        existing = db.scalar(select(TagAudit).where(TagAudit.idempotency_key == payload.idempotency_key))
        if existing:
            if existing.bill_id != bill_id or existing.request_payload != request_payload or existing.undone or (existing.action != "suggest" and existing.superseded):
                raise HTTPException(status_code=409, detail="Idempotency key was already used for a different tag revision")
            db.commit()
            return _tag_audit_read(existing)
    audit = _apply_tag(db, bill, payload.strategy, category, tags, provider, confidence, payload.reason, payload.idempotency_key, request_payload)
    db.commit()
    db.refresh(audit)
    return _tag_audit_read(audit)


@app.post("/api/bills/{bill_id}/tags/{audit_id}/undo", response_model=TagAuditRead)
def undo_tag_revision(bill_id: int, audit_id: int, payload: UndoRequest, db: Session = Depends(get_db)):
    _begin_immediate(db)
    request_payload = json.dumps({"bill_id": bill_id, "audit_id": audit_id, **payload.model_dump()}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if payload.idempotency_key:
        existing = db.scalar(select(TagAudit).where(TagAudit.idempotency_key == payload.idempotency_key))
        if existing:
            if existing.bill_id != bill_id or existing.action != "undo" or existing.reverses_audit_id != audit_id or existing.request_payload != request_payload or existing.superseded:
                raise HTTPException(409, "标签撤销请求或当前状态已发生变化，请刷新后重试")
            db.commit()
            return _tag_audit_read(existing)
    bill = db.get(Bill, bill_id)
    audit = db.get(TagAudit, audit_id)
    if not bill or not audit or audit.bill_id != bill_id:
        raise HTTPException(status_code=404, detail="Tag revision not found")
    if audit.undone:
        raise HTTPException(status_code=409, detail="Tag revision has already been undone")
    current = db.scalar(select(TagAudit).where(TagAudit.bill_id == bill_id, TagAudit.superseded.is_(False)).order_by(TagAudit.id.desc()))
    if not current or current.id != audit.id:
        raise HTTPException(status_code=409, detail="Only the current tag revision can be undone")
    try:
        restored_state = json.loads(audit.before_state_json or "{}")
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=409, detail="Tag revision has no valid previous state") from error
    before_state = bill.tag_state_json
    before_category = bill.category
    restored_assignments = _state_assignments_for_state(db, restored_state)
    restored_category = audit.before_category
    audit.undone = True
    audit.undone_at = datetime.now()
    audit.superseded = True
    bill.tag_state_json = _tag_state_json(restored_state)
    bill.category = restored_category
    undo_audit = TagAudit(
        bill_id=bill_id,
        category=restored_category,
        tags=",".join(tag.tag_name for tag in restored_assignments),
        tag_state_json=bill.tag_state_json,
        strategy="manual",
        confidence=1.0,
        provider="manual",
        superseded=False,
        action="undo",
        actor="local-user",
        reason=payload.reason,
        before_state_json=before_state,
        before_category=before_category,
        reverses_audit_id=audit.id,
        idempotency_key=payload.idempotency_key,
        request_payload=request_payload,
        created_at=datetime.now(),
    )
    db.add(undo_audit)
    db.commit()
    db.refresh(undo_audit)
    return _tag_audit_read(undo_audit)


@app.get("/api/bills/{bill_id}/tags", response_model=list[TagAuditRead])
def list_tag_audits(bill_id: int, db: Session = Depends(get_db)):
    if not db.get(Bill, bill_id):
        raise HTTPException(status_code=404, detail="Bill not found")
    audits = db.scalars(select(TagAudit).where(TagAudit.bill_id == bill_id).order_by(TagAudit.created_at.desc(), TagAudit.id.desc())).all()
    return [_tag_audit_read(audit) for audit in audits]


@app.put("/api/transactions/{bill_id}/account")
def revise_account(bill_id: int, payload: AccountRevisionRequest, db: Session = Depends(get_db)):
    _begin_immediate(db)
    request_payload = json.dumps(payload.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    existing = db.scalar(select(AccountRevision).where(AccountRevision.idempotency_key == payload.idempotency_key))
    if existing:
        if existing.bill_id != bill_id or existing.request_payload != request_payload or existing.undone:
            db.rollback()
            raise HTTPException(status_code=409, detail="Idempotency key was already used for a different account revision")
        db.commit()
        return {"revision_id": existing.id, "bill_id": bill_id, "account_name": existing.after_account, "action": existing.action, "actor": existing.actor, "reason": existing.reason}
    bill = db.get(Bill, bill_id)
    if not bill:
        raise HTTPException(status_code=404, detail="Transaction not found")
    _assert_account_editable(db, bill)
    selected_accounts = db.scalars(
        select(Account).where(Account.display_name == payload.account_name)
    ).all()
    selected_id = selected_accounts[0].id if len(selected_accounts) == 1 else None
    revision = AccountRevision(
        bill_id=bill_id,
        before_account=bill.account_name,
        after_account=payload.account_name,
        before_account_id=bill.account_id,
        after_account_id=selected_id,
        action="confirm",
        actor="local-user",
        reason=payload.reason,
        idempotency_key=payload.idempotency_key,
        request_payload=request_payload,
        created_at=datetime.now(),
    )
    bill.account_name = payload.account_name
    bill.account_id = selected_id
    db.add(revision)
    db.commit()
    db.refresh(revision)
    return {"revision_id": revision.id, "bill_id": bill_id, "account_name": bill.account_name, "action": revision.action, "actor": revision.actor, "reason": revision.reason}


@app.post("/api/transactions/{bill_id}/account-revisions/{revision_id}/undo")
def undo_account_revision(bill_id: int, revision_id: int, payload: UndoRequest, db: Session = Depends(get_db)):
    _begin_immediate(db)
    request_payload = json.dumps({"bill_id": bill_id, "revision_id": revision_id, **payload.model_dump()}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if payload.idempotency_key:
        existing = db.scalar(select(AccountRevision).where(AccountRevision.idempotency_key == payload.idempotency_key))
        if existing:
            if existing.bill_id != bill_id or existing.action != "undo" or existing.reverses_revision_id != revision_id or existing.request_payload != request_payload:
                raise HTTPException(409, "账户撤销请求已用于其他操作")
            latest = db.scalar(select(AccountRevision.id).where(AccountRevision.bill_id == bill_id).order_by(AccountRevision.id.desc()).limit(1)) or 0
            if latest != existing.id:
                raise HTTPException(409, "账户已有后续修订，请刷新后重试")
            db.commit()
            return {"revision_id": existing.id, "bill_id": bill_id, "account_name": existing.after_account, "action": existing.action, "actor": existing.actor, "reason": existing.reason, "reverses_revision_id": revision_id}
    bill = db.get(Bill, bill_id)
    revision = db.get(AccountRevision, revision_id)
    if not bill or not revision or revision.bill_id != bill_id:
        raise HTTPException(status_code=404, detail="Account revision not found")
    _assert_account_editable(db, bill)
    if revision.undone:
        raise HTTPException(status_code=409, detail="Account revision has already been undone")
    current = db.scalar(select(AccountRevision).where(
        AccountRevision.bill_id == bill_id,
        AccountRevision.action == "confirm",
        AccountRevision.undone.is_(False),
    ).order_by(AccountRevision.id.desc()))
    if not current or current.id != revision.id:
        raise HTTPException(status_code=409, detail="Only the current account revision can be undone")
    revision.undone = True
    revision.undone_at = datetime.now()
    bill.account_name = revision.before_account
    bill.account_id = revision.before_account_id
    reversal = AccountRevision(
        bill_id=bill_id,
        before_account=revision.after_account,
        after_account=revision.before_account,
        action="undo",
        actor="local-user",
        reason=payload.reason,
        reverses_revision_id=revision.id,
        idempotency_key=payload.idempotency_key,
        request_payload=request_payload,
        created_at=datetime.now(),
    )
    db.add(reversal)
    db.commit()
    db.refresh(reversal)
    return {"revision_id": reversal.id, "bill_id": bill_id, "account_name": bill.account_name, "action": reversal.action, "actor": reversal.actor, "reason": reversal.reason, "reverses_revision_id": revision.id}


@app.get("/api/transactions/{bill_id}/account-revisions")
def list_account_revisions(bill_id: int, db: Session = Depends(get_db)):
    if not db.get(Bill, bill_id):
        raise HTTPException(status_code=404, detail="Transaction not found")
    revisions = db.scalars(select(AccountRevision).where(AccountRevision.bill_id == bill_id).order_by(AccountRevision.id)).all()
    return [{"revision_id": revision.id, "bill_id": revision.bill_id, "before_account": revision.before_account, "after_account": revision.after_account, "action": revision.action, "actor": revision.actor, "reason": revision.reason, "reverses_revision_id": revision.reverses_revision_id, "undone": revision.undone, "undone_at": revision.undone_at, "created_at": revision.created_at} for revision in revisions]


def _begin_immediate(db: Session) -> None:
    try:
        db.execute(text("BEGIN IMMEDIATE"))
    except OperationalError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Ledger is busy; retry the review operation") from error


def _candidate_snapshot(candidate: ReviewCandidate, members: list[Bill]) -> str:
    return json.dumps({
        "candidate": {
            "status": candidate.status,
            "transfer_group_id": candidate.transfer_group_id,
            "transfer_kind": candidate.transfer_kind,
            "retained_bill_id": candidate.retained_bill_id,
            "resolved_at": candidate.resolved_at.isoformat() if candidate.resolved_at else None,
        },
        "bills": {
            str(bill.id): {
                "aggregate_excluded": bill.aggregate_excluded,
                "transfer_group_id": bill.transfer_group_id,
                "duplicate_of_id": bill.duplicate_of_id,
            } for bill in members
        },
    }, ensure_ascii=False)


ACTIVE_EXCLUSION_STATES = {"duplicate_excluded", "personal_transfer_grouped", "third_party_transfer_grouped", "transfer_grouped", "legacy_transfer_excluded"}


def _assert_review_available(db: Session, candidate: ReviewCandidate, bill_ids: set[int]) -> None:
    assert_no_matters(db, bill_ids)
    for other in db.scalars(select(ReviewCandidate).where(ReviewCandidate.status.in_(ACTIVE_EXCLUSION_STATES), ReviewCandidate.id != candidate.id)).all():
        if bill_ids & set(_candidate_member_ids(other)):
            raise HTTPException(409, f"金额已用于候选 {other.id} 的有效决定；请先撤销或修改该决定")
    if db.scalar(select(RefundDesignation.bill_id).where(RefundDesignation.bill_id.in_(bill_ids)).limit(1)) or db.scalar(select(RefundAllocation.id).where(RefundAllocation.status == "confirmed", (RefundAllocation.refund_bill_id.in_(bill_ids)) | (RefundAllocation.expense_bill_id.in_(bill_ids))).limit(1)):
        raise HTTPException(409, "流水涉及退款性质或有效退款分配，请先在退款记录中处理")


def _assert_account_editable(db: Session, bill: Bill) -> None:
    assert_no_matters(db, {bill.id})
    for candidate in db.scalars(select(ReviewCandidate).where(ReviewCandidate.status.in_(ACTIVE_EXCLUSION_STATES))).all():
        if bill.id in _candidate_member_ids(candidate):
            raise HTTPException(409, f"账户用于已确认候选 {candidate.id}；请先撤销该决定再修改账户")


def _rebuild_review_effects(db: Session, bill_ids: set[int]) -> None:
    """Recompute only affected facts from remaining decisions, never old snapshots."""
    bills = {bill.id: bill for bill in db.scalars(select(Bill).where(Bill.id.in_(bill_ids))).all()}
    for bill in bills.values():
        bill.aggregate_excluded, bill.duplicate_of_id, bill.transfer_group_id = (
            bill.import_nature == "neutral",
            None,
            None,
        )
    for candidate in db.scalars(select(ReviewCandidate).where(ReviewCandidate.status.in_(ACTIVE_EXCLUSION_STATES)).order_by(ReviewCandidate.id)).all():
        for bill_id in bill_ids & set(_candidate_member_ids(candidate)):
            bill = bills.get(bill_id)
            if not bill:
                continue
            if candidate.status == "duplicate_excluded":
                if bill.id != candidate.retained_bill_id:
                    bill.aggregate_excluded, bill.duplicate_of_id = True, candidate.retained_bill_id
            else:
                bill.aggregate_excluded, bill.transfer_group_id = True, candidate.transfer_group_id


def _review_warnings(db: Session) -> list[dict]:
    warnings = []
    owners = {}
    for candidate in db.scalars(select(ReviewCandidate).where(ReviewCandidate.status.in_(ACTIVE_EXCLUSION_STATES))).all():
        if candidate.status in {"third_party_transfer_grouped", "legacy_transfer_excluded"}:
            warnings.append({"candidate_id": candidate.id, "reason": "历史整笔排除缺少新的金额分配依据，请核验后改为手工事项"})
        for bill_id in _candidate_member_ids(candidate):
            if bill_id in owners:
                warnings.append({"candidate_id": candidate.id, "conflicts_with": owners[bill_id], "bill_id": bill_id, "reason": "历史有效决定重复占用同一流水；请撤销错误决定"})
            owners[bill_id] = candidate.id
    return warnings


@app.get("/api/review-warnings")
def review_warnings(db: Session = Depends(get_db)):
    return _review_warnings(db)


def _apply_candidate_decision(db: Session, candidate: ReviewCandidate, payload: CandidateDecision) -> None:
    if payload.action == "confirm_third_party_transfer":
        raise HTTPException(422, "代收代付不能整笔排除。请建立手工事项，填写往来对象并分配本金、回款和费用")
    request_payload = json.dumps(payload.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if payload.idempotency_key:
        existing = db.scalar(select(CandidateActionLog).where(CandidateActionLog.idempotency_key == payload.idempotency_key))
        if existing:
            if existing.candidate_id != candidate.id or existing.request_payload != request_payload or existing.undone:
                raise HTTPException(status_code=409, detail="Idempotency key conflicts with another candidate decision or state")
            return
    latest = db.scalar(select(CandidateActionLog.id).where(CandidateActionLog.candidate_id == candidate.id).order_by(CandidateActionLog.id.desc()).limit(1)) or 0
    if payload.expected_action_id is not None and payload.expected_action_id != latest:
        raise HTTPException(409, "候选已发生变化，请重新打开后处理")
    if payload.expected_member_ids is not None and set(payload.expected_member_ids) != set(_candidate_member_ids(candidate)):
        raise HTTPException(409, "候选成员已有变化，请重新核对全部流水后确认")
    if candidate.status == "duplicate_excluded" and payload.action == "resolve_duplicate" and candidate.retained_bill_id == payload.retained_bill_id:
        return
    if candidate.status == "duplicate_rejected" and payload.action == "reject_duplicate":
        return
    actionable_statuses = {"pending", "evidence_insufficient", "legacy_duplicate_needs_review"}
    if candidate.status not in actionable_statuses:
        raise HTTPException(status_code=409, detail="Candidate has already been handled; undo it before applying another decision")
    members = _candidate_members(db, candidate)
    first, second = db.get(Bill, candidate.bill_id), db.get(Bill, candidate.related_bill_id)
    if not first or not second or len(members) < 2:
        raise HTTPException(status_code=409, detail="Candidate evidence is incomplete")
    if payload.action in {"confirm_transfer", "confirm_personal_transfer", "confirm_third_party_transfer", "resolve_duplicate"}:
        _assert_review_available(db, candidate, {bill.id for bill in members})
    log = CandidateActionLog(candidate_id=candidate.id, action=payload.action, before_state=_candidate_snapshot(candidate, members), actor="local-user", reason=payload.reason or candidate.reason, idempotency_key=payload.idempotency_key, request_payload=request_payload, created_at=datetime.now())
    db.add(log)
    if payload.action in {"confirm_transfer", "confirm_personal_transfer", "confirm_third_party_transfer"}:
        if candidate.candidate_type != "transfer":
            raise HTTPException(status_code=422, detail="Only transfer candidates can be grouped as transfers")
        transfer_kind = "third_party" if payload.action == "confirm_third_party_transfer" else "personal"
        if cents(first.amount) == 0 or cents(first.amount) != -cents(second.amount):
            raise HTTPException(422, "两笔转移必须同额反向；分次付款和手续费请使用手工事项分配")
        if transfer_kind == "personal" and not _has_distinct_account_evidence(first, second):
            raise HTTPException(status_code=422, detail="Transfer confirmation requires two distinct transaction accounts")
        candidate.transfer_group_id = f"transfer-{candidate.id}"
        candidate.transfer_kind = transfer_kind
        candidate.status = f"{transfer_kind}_transfer_grouped"
        for bill in (first, second):
            bill.transfer_group_id = candidate.transfer_group_id
            bill.aggregate_excluded = True
    elif payload.action == "resolve_duplicate":
        if candidate.candidate_type != "duplicate":
            raise HTTPException(status_code=422, detail="Only duplicate candidates can resolve a retained bill")
        if len({cents(bill.amount) for bill in members}) != 1:
            raise HTTPException(422, "重复组的金额或方向不一致，请重新核对来源")
        if payload.retained_bill_id not in {bill.id for bill in members}:
            raise HTTPException(status_code=422, detail="Select one of the duplicate-group bills to retain")
        retained = next(bill for bill in members if bill.id == payload.retained_bill_id)
        candidate.retained_bill_id = retained.id
        candidate.status = "duplicate_excluded"
        for excluded in members:
            if excluded.id != retained.id:
                excluded.aggregate_excluded = True
                excluded.duplicate_of_id = retained.id
    elif payload.action == "reject_duplicate":
        if candidate.candidate_type != "duplicate":
            raise HTTPException(status_code=422, detail="Only duplicate candidates can reject a duplicate suggestion")
        candidate.status = "duplicate_rejected"
    else:
        candidate.status = payload.action
    candidate.resolved_at = datetime.now()
    log.after_state = _candidate_snapshot(candidate, members)


@app.post("/api/candidates/{candidate_id}", response_model=ReviewCandidateRead)
def decide_candidate(candidate_id: int, payload: CandidateDecision, db: Session = Depends(get_db)):
    _begin_immediate(db)
    _consolidate_duplicate_candidates(db)
    candidate = _canonical_candidate(db, db.get(ReviewCandidate, candidate_id))
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    _apply_candidate_decision(db, candidate, payload)
    db.commit()
    db.refresh(candidate)
    return candidate_read(db, candidate)


@app.post("/api/candidates/{candidate_id}/undo", response_model=ReviewCandidateRead)
def undo_candidate(candidate_id: int, payload: UndoRequest | None = None, expected_action_id: int | None = Query(default=None, ge=0), db: Session = Depends(get_db)):
    payload = payload or UndoRequest()
    _begin_immediate(db)
    _consolidate_duplicate_candidates(db)
    candidate = _canonical_candidate(db, db.get(ReviewCandidate, candidate_id))
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    request_payload = json.dumps({"candidate_id": candidate.id, "expected_action_id": expected_action_id, **payload.model_dump()}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if payload.idempotency_key:
        existing = db.scalar(select(CandidateActionLog).where(CandidateActionLog.idempotency_key == payload.idempotency_key))
        if existing:
            if existing.candidate_id != candidate.id or existing.action != "undo" or existing.request_payload != request_payload:
                raise HTTPException(409, "候选撤销请求已用于其他操作")
            latest = db.scalar(select(CandidateActionLog.id).where(CandidateActionLog.candidate_id == candidate.id).order_by(CandidateActionLog.id.desc()).limit(1)) or 0
            if latest != existing.id:
                raise HTTPException(409, "候选已有后续操作，请刷新后重试")
            db.commit()
            return candidate_read(db, candidate)
    latest = db.scalar(select(CandidateActionLog.id).where(CandidateActionLog.candidate_id == candidate.id).order_by(CandidateActionLog.id.desc()).limit(1)) or 0
    if expected_action_id is not None and expected_action_id != latest:
        raise HTTPException(409, "候选已有后续操作，不能撤销另一项决定，请刷新")
    log = db.scalar(select(CandidateActionLog).where(CandidateActionLog.candidate_id == candidate.id, CandidateActionLog.action != "undo", CandidateActionLog.undone.is_(False)).order_by(CandidateActionLog.id.desc()))
    if not log:
        raise HTTPException(status_code=409, detail="No reversible candidate action is available")
    members = _candidate_members(db, candidate)
    current_snapshot = _candidate_snapshot(candidate, members)
    snapshot = json.loads(log.before_state)
    previous = snapshot["candidate"]
    candidate.status = previous["status"]
    candidate.transfer_group_id = previous["transfer_group_id"]
    candidate.transfer_kind = previous.get("transfer_kind")
    candidate.retained_bill_id = previous["retained_bill_id"]
    candidate.resolved_at = datetime.fromisoformat(previous["resolved_at"]) if previous["resolved_at"] else None
    db.flush()
    _rebuild_review_effects(db, {int(bill_id) for bill_id in snapshot["bills"]})
    log.undone = True
    log.undone_at = datetime.now()
    db.add(CandidateActionLog(
        candidate_id=candidate.id,
        action="undo",
        before_state=current_snapshot,
        after_state=_candidate_snapshot(candidate, members),
        actor="local-user",
        reason=payload.reason or "Undo candidate decision",
        reverses_action_id=log.id,
        idempotency_key=payload.idempotency_key,
        request_payload=request_payload,
        created_at=datetime.now(),
    ))
    db.commit()
    db.refresh(candidate)
    return candidate_read(db, candidate)


@app.get("/api/candidates/{candidate_id}/detail")
def candidate_detail(candidate_id: int, db: Session = Depends(get_db)):
    _consolidate_duplicate_candidates(db)
    candidate = _canonical_candidate(db, db.get(ReviewCandidate, candidate_id))
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    def detail_for(bill_id: int):
        bill = db.get(Bill, bill_id)
        origin = db.scalar(select(LedgerOrigin).where(LedgerOrigin.bill_id == bill_id))
        batch = db.get(ImportBatch, origin.import_batch_id) if origin and origin.import_batch_id else None
        try:
            raw_fields = json.loads(origin.raw_payload) if origin and origin.raw_payload else {}
        except json.JSONDecodeError:
            raw_fields = {"unparsed": origin.raw_payload}
        return {
            "bill": bill_read(db, bill),
            "source": {
                "source_type": origin.source_type if origin else "manual",
                "source_reference": origin.source_reference if origin else None,
                "import_batch_id": origin.import_batch_id if origin else None,
                "batch_filename": batch.filename if batch else None,
                "batch_imported_at": batch.imported_at if batch else None,
            },
            "raw_fields": raw_fields,
        }

    return {
        "candidate": candidate_read(db, candidate),
        "first": detail_for(candidate.bill_id),
        "second": detail_for(candidate.related_bill_id),
        "members": [detail_for(bill.id) for bill in _candidate_members(db, candidate)],
        "match_basis": candidate.reason,
        "decision_help": (
            [
                {"action": "保留 A/B", "effect": "只保留所选流水参与收入、支出、净额和趋势；同组其他原始流水保留但不计入汇总。"},
                {"action": "不是重复（拒绝建议）", "effect": "同组所有流水继续计入汇总，不删除或修改原始字段。"},
                {"action": "稍后处理", "effect": "保持待处理的统计状态，汇总不变。"},
                {"action": "撤销/回退", "effect": "恢复该次处理前的候选状态与同组所有流水聚合字段。"},
            ] if candidate.candidate_type == "duplicate" else []
        ),
        "actions": list_candidate_actions(candidate.id, db),
    }


@app.get("/api/candidates/{candidate_id}/actions")
def list_candidate_actions(candidate_id: int, db: Session = Depends(get_db)):
    _consolidate_duplicate_candidates(db)
    candidate = _canonical_candidate(db, db.get(ReviewCandidate, candidate_id))
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    logs = db.scalars(select(CandidateActionLog).where(CandidateActionLog.candidate_id == candidate.id).order_by(CandidateActionLog.created_at.desc(), CandidateActionLog.id.desc())).all()
    return [{"id": log.id, "action": log.action, "actor": log.actor, "reason": log.reason, "before_state": json.loads(log.before_state), "after_state": json.loads(log.after_state) if log.after_state else None, "reverses_action_id": log.reverses_action_id, "idempotency_key": log.idempotency_key, "created_at": log.created_at, "undone": log.undone, "undone_at": log.undone_at} for log in logs]


@app.delete("/api/bills/{bill_id}", status_code=204)
def delete_bill(bill_id: int, db: Session = Depends(get_db)):
    bill = db.get(Bill, bill_id)
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    raise HTTPException(status_code=409, detail="账本事实不可删除；请通过复核修正解释并保留审计")


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
