from __future__ import annotations

import base64
import json
import re
from contextlib import asynccontextmanager
from datetime import date, datetime, time
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import MetaData, Table, asc, desc, func, inspect, select
from sqlalchemy.orm import Session

from app.config import APP_DISPLAY_NAME, APP_SLUG
from app.database import AssetSnapshot, Bill, BillViewTag, CandidateActionLog, ImportArtifact, ImportBatch, LedgerOrigin, ReviewCandidate, SessionLocal, TagAudit, TagChangeLog, TagView, ViewTag, init_db
from app.file_import import normalise_rows, parse_upload, preview_rows
from app.schemas import AssetCreate, AssetRead, BatchImportItemRead, BatchImportRead, BatchImportRequest, BatchPreviewItemRead, BatchPreviewRead, BillCreate, BillRead, CandidateBatchDecision, CandidateDecision, CandidatePageRead, ImportBatchRead, ImportPreviewRead, ReviewCandidateRead, TagApply, TagAuditRead, TagRequest, TagResult, TagStateAssignmentRequest, TagStateBulkAssignmentRequest, TagViewCreate, TagViewRead, TagViewUpdate, TransactionPageRead, ViewTagAssignmentRead, ViewTagAssignmentRequest, ViewTagCreate, ViewTagRead, ViewTagUpdate
from app.tagging import classify, classify_rules

APP_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with SessionLocal() as db:
        _consolidate_duplicate_candidates(db)
        db.commit()
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
    "asset_snapshots",
    "bill_tags",
    "bill_view_tags",
    "bills",
    "candidate_action_logs",
    "import_artifacts",
    "import_batches",
    "ledger_origins",
    "review_candidates",
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


def _generated_system_name(db: Session, model, prefix: str, view_id: int | None = None) -> str:
    for number in range(1, 100000):
        candidate = f"{prefix}_{number}"
        statement = select(model.id).where(model.system_name == candidate)
        if view_id is not None:
            statement = statement.where(model.view_id == view_id)
        if not db.scalar(statement.limit(1)):
            return candidate
    raise HTTPException(status_code=409, detail="No available system name")


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
) -> TagAudit:
    state = _validate_tag_state(db, submitted)
    current = db.scalar(select(TagAudit).where(
        TagAudit.bill_id == bill.id,
        TagAudit.superseded.is_(False),
    ).order_by(TagAudit.confidence.desc(), TagAudit.id.desc()))
    superseded = bool(current and confidence < current.confidence)
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
        created_at=datetime.now(),
    )
    if not superseded:
        if current:
            current.superseded = True
        bill.tag_state_json = audit.tag_state_json
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
        category=bill.category,
        tags=[tag.tag_name for tag in assignments],
        source_type=origin.source_type if origin else None,
        source_reference=origin.source_reference if origin else None,
        import_batch_id=origin.import_batch_id if origin else None,
        account_name=bill.account_name,
        direction="收入" if bill.amount >= 0 else "支出",
        aggregate_excluded=bill.aggregate_excluded,
        transfer_group_id=bill.transfer_group_id,
        duplicate_of_id=bill.duplicate_of_id,
        view_tags=assignments,
        tag_state=state,
    )


def _bill_view_tags(db: Session, bill_id: int) -> list[ViewTagAssignmentRead]:
    bill = db.get(Bill, bill_id)
    return _state_assignments(db, bill) if bill else []


def _tag_view_read(db: Session, view: TagView) -> TagViewRead:
    tags = db.scalars(select(ViewTag).where(ViewTag.view_id == view.id).order_by(ViewTag.is_unclassified.desc(), ViewTag.name)).all()
    return TagViewRead(id=view.id, name=view.name, system_name=view.system_name, archived=view.archived, tags=[ViewTagRead(id=tag.id, name=tag.name, system_name=tag.system_name, is_unclassified=tag.is_unclassified, archived=tag.archived) for tag in tags])


def _normalise_tags(tags: list[str]) -> list[str]:
    return list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))


def _apply_tag(db: Session, bill: Bill, strategy: str, category: str, tags: list[str], provider: str, confidence: float) -> TagAudit:
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
    return _write_tag_state(db, bill, state, strategy, confidence, provider)


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


def _duplicate_component(db: Session, bill: Bill) -> list[Bill]:
    matches = db.scalars(select(Bill).where(Bill.merchant == bill.merchant, Bill.amount == bill.amount).order_by(Bill.occurred_at, Bill.id)).all()
    components: list[list[Bill]] = []
    for match in matches:
        if not components or (match.occurred_at - components[-1][-1].occurred_at).total_seconds() > 300:
            components.append([match])
        else:
            components[-1].append(match)
    return next((component for component in components if any(member.id == bill.id for member in component)), [bill])


def _consolidate_duplicate_candidates(db: Session) -> None:
    candidates = db.scalars(select(ReviewCandidate).where(ReviewCandidate.candidate_type == "duplicate", ReviewCandidate.status != "superseded_duplicate_group").order_by(ReviewCandidate.id)).all()
    groups: list[set[int]] = []
    grouped_candidates: list[list[ReviewCandidate]] = []
    for candidate in candidates:
        ids: set[int] = set()
        for bill_id in _candidate_member_ids(candidate):
            bill = db.get(Bill, bill_id)
            if bill:
                ids.update(member.id for member in _duplicate_component(db, bill))
        related = [index for index, member_ids in enumerate(groups) if ids & member_ids]
        if not related:
            groups.append(ids); grouped_candidates.append([candidate]); continue
        target = related[0]
        groups[target].update(ids); grouped_candidates[target].append(candidate)
        for index in reversed(related[1:]):
            groups[target].update(groups.pop(index)); grouped_candidates[target].extend(grouped_candidates.pop(index))
    for ids, group_candidates in zip(groups, grouped_candidates):
        members = sorted((db.get(Bill, bill_id) for bill_id in ids), key=lambda bill: (bill.occurred_at, bill.id))
        logs_by_candidate = {candidate.id: db.scalars(select(CandidateActionLog).where(CandidateActionLog.candidate_id == candidate.id).order_by(CandidateActionLog.created_at.desc(), CandidateActionLog.id.desc())).all() for candidate in group_candidates}
        processed = [candidate for candidate in group_candidates if candidate.status not in {"pending", "legacy_duplicate_needs_review"}]
        canonical = max(processed, key=lambda candidate: (logs_by_candidate[candidate.id][0].created_at if logs_by_candidate[candidate.id] else candidate.created_at, candidate.id), default=group_candidates[0])
        fingerprint = "duplicate:" + ":".join(str(member.id) for member in members)
        canonical.bill_id, canonical.related_bill_id = members[0].id, members[1].id
        canonical.member_bill_ids = json.dumps([member.id for member in members])
        canonical.group_fingerprint = fingerprint
        if canonical.status == "duplicate_excluded" and canonical.retained_bill_id in ids:
            for member in members:
                member.aggregate_excluded = member.id != canonical.retained_bill_id
                member.duplicate_of_id = canonical.retained_bill_id if member.id != canonical.retained_bill_id else None
        for duplicate in group_candidates:
            if duplicate.id == canonical.id:
                continue
            for log in logs_by_candidate[duplicate.id]:
                log.candidate_id = canonical.id
            duplicate.status = "superseded_duplicate_group"
            duplicate.superseded_by_id = canonical.id
            duplicate.group_fingerprint = fingerprint


def _generate_candidates(db: Session, bill: Bill) -> int:
    created = 0
    prior_bills = db.scalars(select(Bill).where(Bill.id != bill.id)).all()
    for other in prior_bills:
        seconds_apart = abs((bill.occurred_at - other.occurred_at).total_seconds())
        if seconds_apart > 300 or abs(abs(bill.amount) - abs(other.amount)) > 0.01:
            continue
        if bill.amount == other.amount and bill.merchant == other.merchant:
            members = _duplicate_component(db, bill)
            if len(members) < 2:
                continue
            candidate_type, confidence, reason, status = "duplicate", 0.92, f"{len(members)} 笔金额、交易方一致且相邻时间不超过 5 分钟；作为同一重复候选组处理", "pending"
            member_ids = {member.id for member in members}
            pending_duplicates = db.scalars(select(ReviewCandidate).where(ReviewCandidate.candidate_type == "duplicate", ReviewCandidate.status.in_(("pending", "legacy_duplicate_needs_review")))).all()
            existing = next((candidate for candidate in pending_duplicates if member_ids & set(_candidate_member_ids(candidate))), None)
            if existing:
                existing.bill_id, existing.related_bill_id = members[0].id, members[1].id
                existing.member_bill_ids = json.dumps([member.id for member in members])
                existing.group_fingerprint = "duplicate:" + ":".join(str(member.id) for member in members)
                existing.reason = reason
                return 0
        elif bill.amount * other.amount < 0:
            candidate_type = "transfer"
            if _has_distinct_account_evidence(bill, other):
                confidence, reason, status = 0.78, "5 分钟内同额反向、不同账户；可确认个人账户间转移", "pending"
            else:
                confidence, reason, status = 0.42, "5 分钟内同额反向，但缺少两个不同账户证据；仅供人工核验，不能自动认定为个人转移", "evidence_insufficient"
        else:
            continue
        db.add(ReviewCandidate(candidate_type=candidate_type, bill_id=members[0].id if candidate_type == "duplicate" else bill.id, related_bill_id=members[1].id if candidate_type == "duplicate" else other.id, member_bill_ids=json.dumps([member.id for member in members]) if candidate_type == "duplicate" else "", group_fingerprint=("duplicate:" + ":".join(str(member.id) for member in members)) if candidate_type == "duplicate" else "", confidence=confidence, reason=reason, status=status, created_at=datetime.now()))
        created += 1
        if candidate_type == "duplicate":
            return created
    return created


def _has_distinct_account_evidence(first: Bill, second: Bill) -> bool:
    unknown_accounts = {"", "未提供账户", "手工未提供账户"}
    return first.account_name not in unknown_accounts and second.account_name not in unknown_accounts and first.account_name != second.account_name


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
        candidate_type=candidate.candidate_type,
        confidence=candidate.confidence,
        reason=candidate.reason,
        status=candidate.status,
        transfer_group_id=candidate.transfer_group_id,
        transfer_kind=candidate.transfer_kind,
        retained_bill_id=candidate.retained_bill_id,
        resolved_at=candidate.resolved_at,
        undo_available=bool(db.scalar(select(CandidateActionLog.id).where(CandidateActionLog.candidate_id == candidate.id, CandidateActionLog.undone.is_(False)).order_by(CandidateActionLog.id.desc()))),
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
    return ImportPreviewRead(
        source_type=source_type,
        filename=parsed.filename,
        file_format=parsed.file_format,
        archive_entry=parsed.archive_entry,
        file_sha256=parsed.file_sha256,
        row_count=len(parsed.rows),
        columns=["交易时间", "交易方", "金额", "备注", "收支", "流水号"],
        preview_rows=preview_rows(parsed),
    )


def _batch_read(batch: ImportBatch, parsed, candidate_count: int) -> ImportBatchRead:
    return ImportBatchRead(
        id=batch.id,
        source_type=batch.source_type,
        filename=batch.filename,
        imported_at=batch.imported_at,
        row_count=batch.row_count,
        imported_count=batch.imported_count,
        candidate_count=candidate_count,
        file_sha256=parsed.file_sha256,
        file_format=parsed.file_format,
        archive_entry=parsed.archive_entry,
        batch_token=batch.batch_token,
    )


def _commit_parsed(db: Session, source_type: str, parsed, batch_token: str | None = None) -> ImportBatchRead:
    duplicate = db.scalar(select(ImportArtifact).where(ImportArtifact.source_type == source_type, ImportArtifact.sha256 == parsed.file_sha256))
    if duplicate:
        raise ValueError("该来源文件已导入，已跳过重复文件")
    imported_rows = normalise_rows(parsed)
    batch = ImportBatch(source_type=source_type, filename=parsed.filename, imported_at=datetime.now(), row_count=len(imported_rows), imported_count=0, batch_token=batch_token)
    db.add(batch)
    db.flush()
    db.add(ImportArtifact(import_batch_id=batch.id, source_type=source_type, filename=parsed.filename, file_format=parsed.file_format, archive_entry=parsed.archive_entry, sha256=parsed.file_sha256))
    candidate_count = 0
    for row in imported_rows:
        bill = Bill(occurred_at=row.occurred_at, merchant=row.merchant, note=row.note, amount=row.amount, account_name=row.account_name, category="未分类", tags="")
        db.add(bill)
        db.flush()
        db.add(LedgerOrigin(bill_id=bill.id, source_type=source_type, source_reference=row.reference, raw_payload=row.raw_payload, import_batch_id=batch.id))
        category, tags, provider = classify_rules(row.merchant, row.note)
        _apply_tag(db, bill, "local_rules", category, tags, provider, STRATEGY_CONFIDENCE["local_rules"])
        candidate_count += _generate_candidates(db, bill)
        batch.imported_count += 1
    db.commit()
    return _batch_read(batch, parsed, candidate_count)


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


@app.get("/api/bills", response_model=list[BillRead])
def list_bills(db: Session = Depends(get_db)):
    return [bill_read(db, bill) for bill in db.scalars(select(Bill).order_by(Bill.occurred_at.desc())).all()]


@app.get("/api/transactions", response_model=TransactionPageRead)
def list_transactions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    sort_by: str = Query(default="occurred_at", pattern="^(occurred_at|amount)$"),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    date_from: date | None = None,
    date_to: date | None = None,
    amount_min: float | None = Query(default=None, ge=0),
    amount_max: float | None = Query(default=None, ge=0),
    source: list[str] = Query(default=[]),
    direction: str | None = Query(default=None, pattern="^(income|expense|transfer)$"),
    q: str | None = Query(default=None, max_length=200),
    tag: list[str] = Query(default=[]),
    db: Session = Depends(get_db),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be before date_to")
    if amount_min is not None and amount_max is not None and amount_min > amount_max:
        raise HTTPException(status_code=422, detail="amount_min must not exceed amount_max")
    clauses = []
    if date_from:
        clauses.append(Bill.occurred_at >= datetime.combine(date_from, time.min))
    if date_to:
        clauses.append(Bill.occurred_at <= datetime.combine(date_to, time.max))
    if amount_min is not None:
        clauses.append(func.abs(Bill.amount) >= amount_min)
    if amount_max is not None:
        clauses.append(func.abs(Bill.amount) <= amount_max)
    if source:
        clauses.append(Bill.id.in_(select(LedgerOrigin.bill_id).where(LedgerOrigin.source_type.in_(source))))
    if direction == "income":
        clauses.append(Bill.amount > 0)
    elif direction == "expense":
        clauses.append(Bill.amount < 0)
    elif direction == "transfer":
        clauses.append(Bill.transfer_group_id.is_not(None))
    if q:
        needle = f"%{q.strip()}%"
        clauses.append((Bill.merchant.ilike(needle)) | (Bill.note.ilike(needle)))
    selected_by_view: dict[str, str] = {}
    for selector in tag:
        try:
            view_selector, tag_selector = selector.split(":", 1)
        except ValueError as error:
            raise HTTPException(status_code=422, detail="tag must use view_system_name:tag_system_name") from error
        if view_selector.isdigit() and tag_selector.isdigit():
            # Compatibility-only support for the previous public endpoint contract.
            legacy_tag = db.get(ViewTag, int(tag_selector))
            legacy_view = db.get(TagView, int(view_selector))
            if not legacy_view or not legacy_tag or legacy_tag.view_id != legacy_view.id:
                raise HTTPException(status_code=422, detail="tag does not belong to the requested view")
            view_system_name, tag_system_name = legacy_view.system_name, legacy_tag.system_name
        else:
            view_system_name = _system_name_or_422(view_selector, "Tag view")
            tag_system_name = _system_name_or_422(tag_selector, "Tag")
            view = db.scalar(select(TagView).where(TagView.system_name == view_system_name, TagView.archived.is_(False)))
            selected_tag = db.scalar(select(ViewTag).join(TagView, TagView.id == ViewTag.view_id).where(
                TagView.system_name == view_system_name,
                ViewTag.system_name == tag_system_name,
                ViewTag.archived.is_(False),
            ))
            if not view or not selected_tag:
                raise HTTPException(status_code=422, detail="tag does not belong to the requested view")
        if view_system_name in selected_by_view and selected_by_view[view_system_name] != tag_system_name:
            raise HTTPException(status_code=400, detail="only one tag may be selected in each view")
        selected_by_view[view_system_name] = tag_system_name
    for view_system_name, tag_system_name in selected_by_view.items():
        clauses.append(func.coalesce(
            func.json_extract(Bill.tag_state_json, f"$.{view_system_name}"),
            "unclassified",
        ) == tag_system_name)
    order_column = Bill.occurred_at if sort_by == "occurred_at" else Bill.amount
    order_fn = asc if sort_order == "asc" else desc
    statement = select(Bill).where(*clauses).order_by(order_fn(order_column), order_fn(Bill.id))
    total = db.scalar(select(func.count(Bill.id)).where(*clauses)) or 0
    bills = db.scalars(statement.offset((page - 1) * page_size).limit(page_size)).all()
    return TransactionPageRead(items=[bill_read(db, bill) for bill in bills], total=total, page=page, page_size=page_size, filters={"date_from": str(date_from) if date_from else None, "date_to": str(date_to) if date_to else None, "amount_min": amount_min, "amount_max": amount_max, "source": source, "direction": direction, "q": q, "tag": tag}, sort={"by": sort_by, "order": sort_order})


@app.get("/api/tag-views", response_model=list[TagViewRead])
def list_tag_views(include_archived: bool = False, db: Session = Depends(get_db)):
    statement = select(TagView).order_by(TagView.id)
    if not include_archived:
        statement = statement.where(TagView.archived.is_(False))
    return [_tag_view_read(db, view) for view in db.scalars(statement).all()]


@app.post("/api/tag-views", response_model=TagViewRead, status_code=201)
def create_tag_view(payload: TagViewCreate, db: Session = Depends(get_db)):
    if db.scalar(select(TagView).where(TagView.name == payload.name.strip())):
        raise HTTPException(status_code=409, detail="Tag view name already exists")
    system_name = payload.system_name or _generated_system_name(db, TagView, "view")
    _system_name_or_422(system_name, "Tag view")
    if db.scalar(select(TagView.id).where(TagView.system_name == system_name)):
        raise HTTPException(status_code=409, detail="Tag view system_name already exists")
    view = TagView(name=payload.name.strip(), system_name=system_name, created_at=datetime.now())
    db.add(view)
    db.flush()
    db.add(ViewTag(view_id=view.id, name="未分类", system_name="unclassified", is_unclassified=True))
    db.add(TagChangeLog(view_id=view.id, tag_id=None, action="create_view", detail=view.name, created_at=datetime.now()))
    db.commit()
    return _tag_view_read(db, view)


@app.patch("/api/tag-views/{view_id}", response_model=TagViewRead)
def update_tag_view(view_id: int, payload: TagViewUpdate, db: Session = Depends(get_db)):
    view = db.get(TagView, view_id)
    if not view:
        raise HTTPException(status_code=404, detail="Tag view not found")
    if payload.name and payload.name.strip() != view.name:
        if db.scalar(select(TagView).where(TagView.name == payload.name.strip(), TagView.id != view.id)):
            raise HTTPException(status_code=409, detail="Tag view name already exists")
        view.name = payload.name.strip()
    if payload.archived is not None:
        view.archived = payload.archived
    db.add(TagChangeLog(view_id=view.id, tag_id=None, action="update_view", detail=view.name, created_at=datetime.now()))
    db.commit()
    return _tag_view_read(db, view)


@app.post("/api/tag-views/{view_id}/tags", response_model=ViewTagRead, status_code=201)
def create_view_tag(view_id: int, payload: ViewTagCreate, db: Session = Depends(get_db)):
    view = db.get(TagView, view_id)
    if not view:
        raise HTTPException(status_code=404, detail="Tag view not found")
    if db.scalar(select(ViewTag).where(ViewTag.view_id == view_id, ViewTag.name == payload.name.strip())):
        raise HTTPException(status_code=409, detail="Tag name already exists in this view")
    system_name = payload.system_name or _generated_system_name(db, ViewTag, "tag", view_id=view_id)
    _system_name_or_422(system_name, "Tag")
    if db.scalar(select(ViewTag.id).where(ViewTag.view_id == view_id, ViewTag.system_name == system_name)):
        raise HTTPException(status_code=409, detail="Tag system_name already exists in this view")
    tag = ViewTag(view_id=view_id, name=payload.name.strip(), system_name=system_name)
    db.add(tag)
    db.flush()
    db.add(TagChangeLog(view_id=view_id, tag_id=tag.id, action="create_tag", detail=tag.name, created_at=datetime.now()))
    db.commit()
    return ViewTagRead(id=tag.id, name=tag.name, system_name=tag.system_name, is_unclassified=tag.is_unclassified, archived=tag.archived)


@app.patch("/api/tag-views/{view_id}/tags/{tag_id}", response_model=ViewTagRead)
def update_view_tag(view_id: int, tag_id: int, payload: ViewTagUpdate, db: Session = Depends(get_db)):
    tag = db.get(ViewTag, tag_id)
    if not tag or tag.view_id != view_id:
        raise HTTPException(status_code=404, detail="Tag not found")
    if tag.is_unclassified and (payload.name or payload.archived is not None):
        raise HTTPException(status_code=422, detail="The unclassified tag is protected")
    if payload.name and payload.name.strip() != tag.name:
        if db.scalar(select(ViewTag).where(ViewTag.view_id == view_id, ViewTag.name == payload.name.strip(), ViewTag.id != tag.id)):
            raise HTTPException(status_code=409, detail="Tag name already exists in this view")
        tag.name = payload.name.strip()
    if payload.archived is not None:
        tag.archived = payload.archived
    db.add(TagChangeLog(view_id=view_id, tag_id=tag.id, action="update_tag", detail=tag.name, created_at=datetime.now()))
    db.commit()
    return ViewTagRead(id=tag.id, name=tag.name, system_name=tag.system_name, is_unclassified=tag.is_unclassified, archived=tag.archived)


@app.delete("/api/tag-views/{view_id}/tags/{tag_id}", status_code=204)
def delete_view_tag(view_id: int, tag_id: int, migrate_to_tag_id: int | None = None, db: Session = Depends(get_db)):
    tag = db.get(ViewTag, tag_id)
    if not tag or tag.view_id != view_id:
        raise HTTPException(status_code=404, detail="Tag not found")
    if tag.is_unclassified:
        raise HTTPException(status_code=422, detail="The unclassified tag is protected")
    assignments = db.scalars(select(BillViewTag).where(BillViewTag.tag_id == tag.id)).all()
    if assignments and migrate_to_tag_id is None:
        raise HTTPException(status_code=422, detail="Used tags require a migration target")
    if migrate_to_tag_id is not None:
        target = db.get(ViewTag, migrate_to_tag_id)
        if not target or target.view_id != view_id:
            raise HTTPException(status_code=422, detail="Migration target must belong to the same view")
        if target.is_unclassified:
            for assignment in assignments:
                db.delete(assignment)
        else:
            for assignment in assignments:
                assignment.tag_id = target.id
    db.add(TagChangeLog(view_id=view_id, tag_id=tag.id, action="delete_tag", detail=f"migrate_to={migrate_to_tag_id}", created_at=datetime.now()))
    db.delete(tag)
    db.commit()


@app.put("/api/transactions/{bill_id}/tag-assignments/{view_id}", response_model=BillRead)
def assign_view_tag(bill_id: int, view_id: int, payload: ViewTagAssignmentRequest, db: Session = Depends(get_db)):
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
    bill = db.get(Bill, bill_id)
    if not bill:
        raise HTTPException(status_code=404, detail="Transaction not found")
    _write_tag_state(db, bill, payload.tag_state, payload.strategy, payload.confidence, "named_tag_state")
    db.commit()
    return bill_read(db, bill)


@app.put("/api/transactions/bulk-tag-state")
def assign_tag_state_bulk(payload: TagStateBulkAssignmentRequest, db: Session = Depends(get_db)):
    bills = db.scalars(select(Bill).where(Bill.id.in_(payload.bill_ids)).order_by(Bill.id)).all()
    if len(bills) != len(set(payload.bill_ids)):
        raise HTTPException(status_code=404, detail="One or more transactions were not found")
    for bill in bills:
        _write_tag_state(db, bill, payload.tag_state, payload.strategy, payload.confidence, "named_tag_state_bulk")
    db.commit()
    return {"updated": len(bills), "bill_ids": [bill.id for bill in bills]}


@app.post("/api/bills", response_model=BillRead, status_code=201)
def create_bill(payload: BillCreate, db: Session = Depends(get_db)):
    category, tags, provider = classify_rules(payload.merchant, payload.note)
    bill = Bill(**payload.model_dump(), category=category, tags="")
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
        created_at=audit.created_at,
        tag_state=state if isinstance(state, dict) else {},
    )


@app.post("/api/bills/{bill_id}/tags", response_model=TagAuditRead, status_code=201)
def apply_tag(bill_id: int, payload: TagApply, db: Session = Depends(get_db)):
    bill = db.get(Bill, bill_id)
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
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
    audit = _apply_tag(db, bill, payload.strategy, category, tags, provider, confidence)
    db.commit()
    db.refresh(audit)
    return _tag_audit_read(audit)


@app.get("/api/bills/{bill_id}/tags", response_model=list[TagAuditRead])
def list_tag_audits(bill_id: int, db: Session = Depends(get_db)):
    if not db.get(Bill, bill_id):
        raise HTTPException(status_code=404, detail="Bill not found")
    audits = db.scalars(select(TagAudit).where(TagAudit.bill_id == bill_id).order_by(TagAudit.created_at.desc(), TagAudit.id.desc())).all()
    return [_tag_audit_read(audit) for audit in audits]


@app.get("/api/candidates", response_model=list[ReviewCandidateRead])
def list_candidates(db: Session = Depends(get_db)):
    _consolidate_duplicate_candidates(db)
    db.commit()
    candidates = db.scalars(select(ReviewCandidate).where(ReviewCandidate.status != "superseded_duplicate_group").order_by(ReviewCandidate.created_at.desc())).all()
    return [candidate_read(db, candidate) for candidate in candidates]


@app.get("/api/candidates/page", response_model=CandidatePageRead)
def page_candidates(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    candidate_type: str | None = None,
    db: Session = Depends(get_db),
):
    _consolidate_duplicate_candidates(db)
    db.commit()
    filters = [ReviewCandidate.status != "superseded_duplicate_group"]
    if status:
        filters.append(ReviewCandidate.status == status)
    if candidate_type:
        filters.append(ReviewCandidate.candidate_type == candidate_type)
    statement = select(ReviewCandidate).where(*filters).order_by(ReviewCandidate.created_at.desc(), ReviewCandidate.id.desc())
    total = db.scalar(select(func.count(ReviewCandidate.id)).where(*filters)) or 0
    candidates = db.scalars(statement.offset((page - 1) * page_size).limit(page_size)).all()
    return CandidatePageRead(items=[candidate_read(db, candidate) for candidate in candidates], total=total, page=page, page_size=page_size)


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


def _apply_candidate_decision(db: Session, candidate: ReviewCandidate, payload: CandidateDecision) -> None:
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
    db.add(CandidateActionLog(candidate_id=candidate.id, action=payload.action, before_state=_candidate_snapshot(candidate, members), created_at=datetime.now()))
    if payload.action in {"confirm_transfer", "confirm_personal_transfer", "confirm_third_party_transfer"}:
        if candidate.candidate_type != "transfer":
            raise HTTPException(status_code=422, detail="Only transfer candidates can be grouped as transfers")
        transfer_kind = "third_party" if payload.action == "confirm_third_party_transfer" else "personal"
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


@app.post("/api/candidates/batch", response_model=list[ReviewCandidateRead])
def decide_candidates_batch(payload: CandidateBatchDecision, db: Session = Depends(get_db)):
    candidate_ids = [item.candidate_id for item in payload.items]
    if len(candidate_ids) != len(set(candidate_ids)):
        raise HTTPException(status_code=422, detail="Each candidate can be processed only once per batch")
    candidates: list[ReviewCandidate] = []
    for item in payload.items:
        candidate = db.get(ReviewCandidate, item.candidate_id)
        if not candidate:
            raise HTTPException(status_code=404, detail=f"Candidate {item.candidate_id} not found")
        _apply_candidate_decision(db, candidate, CandidateDecision(action=item.action, retained_bill_id=item.retained_bill_id))
        candidates.append(candidate)
    db.commit()
    for candidate in candidates:
        db.refresh(candidate)
    return [candidate_read(db, candidate) for candidate in candidates]


@app.post("/api/candidates/{candidate_id}", response_model=ReviewCandidateRead)
def decide_candidate(candidate_id: int, payload: CandidateDecision, db: Session = Depends(get_db)):
    _consolidate_duplicate_candidates(db)
    candidate = _canonical_candidate(db, db.get(ReviewCandidate, candidate_id))
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    _apply_candidate_decision(db, candidate, payload)
    db.commit()
    db.refresh(candidate)
    return candidate_read(db, candidate)


@app.post("/api/candidates/{candidate_id}/undo", response_model=ReviewCandidateRead)
def undo_candidate(candidate_id: int, db: Session = Depends(get_db)):
    _consolidate_duplicate_candidates(db)
    candidate = _canonical_candidate(db, db.get(ReviewCandidate, candidate_id))
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    log = db.scalar(select(CandidateActionLog).where(CandidateActionLog.candidate_id == candidate_id, CandidateActionLog.undone.is_(False)).order_by(CandidateActionLog.id.desc()))
    if not log:
        raise HTTPException(status_code=409, detail="No reversible candidate action is available")
    snapshot = json.loads(log.before_state)
    previous = snapshot["candidate"]
    candidate.status = previous["status"]
    candidate.transfer_group_id = previous["transfer_group_id"]
    candidate.transfer_kind = previous.get("transfer_kind")
    candidate.retained_bill_id = previous["retained_bill_id"]
    candidate.resolved_at = datetime.fromisoformat(previous["resolved_at"]) if previous["resolved_at"] else None
    for bill_id, bill_state in snapshot["bills"].items():
        bill = db.get(Bill, int(bill_id))
        if bill:
            bill.aggregate_excluded = bill_state["aggregate_excluded"]
            bill.transfer_group_id = bill_state["transfer_group_id"]
            bill.duplicate_of_id = bill_state["duplicate_of_id"]
    log.undone = True
    log.undone_at = datetime.now()
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
    return [{"id": log.id, "action": log.action, "created_at": log.created_at, "undone": log.undone, "undone_at": log.undone_at} for log in logs]


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
    ledger_filter = Bill.aggregate_excluded.is_(False)
    income = db.scalar(select(func.coalesce(func.sum(Bill.amount), 0.0)).where(ledger_filter, Bill.amount > 0))
    spending = db.scalar(select(func.coalesce(func.sum(Bill.amount), 0.0)).where(ledger_filter, Bill.amount < 0))
    transfer_group_count = len({group for group in db.scalars(select(Bill.transfer_group_id).where(Bill.transfer_group_id.is_not(None))).all() if group})
    trend_rows = db.execute(select(func.date(Bill.occurred_at), Bill.amount).where(ledger_filter).order_by(func.date(Bill.occurred_at))).all()
    trend: dict[str, dict[str, float | int]] = {}
    for day, amount in trend_rows:
        point = trend.setdefault(str(day), {"income": 0.0, "spending": 0.0, "net": 0.0, "bill_count": 0})
        point["income" if amount > 0 else "spending"] += amount
        point["net"] += amount
        point["bill_count"] += 1
    return {"income": income, "spending": spending, "net": income + spending, "bill_count": db.scalar(select(func.count(Bill.id))), "import_count": db.scalar(select(func.count(LedgerOrigin.id))) or 0, "candidate_count": db.scalar(select(func.count(ReviewCandidate.id)).where(ReviewCandidate.status == "pending")), "transfer_group_count": transfer_group_count, "trend": [{"day": day, **point} for day, point in trend.items()], "generated_at": datetime.now().isoformat()}
