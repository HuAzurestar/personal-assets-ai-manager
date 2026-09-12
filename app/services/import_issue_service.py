from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.errors import ImportIssueCommandError
from app.mappers.ledger_mapper import LedgerMapper
from app.mappers.import_issue_mapper import ImportIssueMapper
from app.schemas import BillRead, IssueResolve, UndoRequest
from app.schemas.import_issue import (
    ImportIssueActionRead,
    ImportIssuePageRead,
    ImportIssueRead,
    ImportIssueStatusRead,
    ImportIssueSummaryRead,
)
from app.services.candidate_suggestion_service import CandidateSuggestionService
from app.services.ledger_service import bill_read_from_vo


class ImportIssueService:
    def __init__(self, db: Session):
        self.db = db
        self.mapper = ImportIssueMapper(db)

    def list(self) -> list[ImportIssueRead]:
        issues = self.mapper.issues()
        self._assert_batches(issues)
        actions = self.mapper.actions([issue.id for issue in issues])
        return self._details(issues, actions)

    def page(self, *, page: int, page_size: int) -> ImportIssuePageRead:
        total, issues = self.mapper.page(page=page, page_size=page_size)
        self._assert_batches(issues)
        return ImportIssuePageRead(
            items=[ImportIssueSummaryRead(
                id=issue.id,
                filename=issue.filename,
                batch_id=issue.batch_id,
                row_number=issue.source_row_number,
                error=issue.error,
                bill_id=issue.bill_id,
                resolved_at=issue.resolved_at,
                status=(
                    "pending"
                    if issue.resolved_at is None
                    else "resolved" if issue.bill_id else "not_posted"
                ),
            ) for issue in issues],
            total=total,
            page=page,
            page_size=page_size,
        )

    def get(self, issue_id: int) -> ImportIssueRead:
        issue = self.mapper.issue(issue_id)
        if not issue:
            raise LookupError("错误记录不存在")
        self._assert_batches((issue,))
        return self._details((issue,), self.mapper.actions([issue.id]))[0]

    def resolve(self, issue_id: int, payload: IssueResolve) -> BillRead:
        self.mapper.begin_immediate()
        try:
            issue = self.mapper.command_issue(issue_id)
            if not issue:
                raise ImportIssueCommandError(404, "错误记录不存在")
            if issue.resolved_at:
                raise ImportIssueCommandError(409, "该记录已处理，请刷新")
            if not issue.source_type:
                raise ImportIssueCommandError(500, "错误记录引用的导入批次不存在")

            bill_id = self.mapper.create_bill(
                payload.model_dump(exclude={"reason"})
            )
            raw = json.loads(issue.raw_payload)
            reference = next((
                raw[key]
                for key in ("交易号", "支付宝交易号", "交易单号", "交易订单号")
                if raw.get(key)
            ), "")
            self.mapper.append_origin(
                bill_id=bill_id,
                source_type=issue.source_type,
                source_reference=reference,
                import_batch_id=issue.batch_id,
                source_row_number=issue.source_row_number,
                raw_payload=issue.raw_payload,
            )
            resolution = json.dumps({
                "actor": "local-user",
                "reason": payload.reason,
                "corrected_fields": payload.model_dump(
                    mode="json",
                    exclude={"reason"},
                ),
            }, ensure_ascii=False, sort_keys=True)
            now = datetime.now()
            self.mapper.resolve_issue(
                issue_id=issue.id,
                bill_id=bill_id,
                resolution=resolution,
                resolved_at=now,
            )
            self.mapper.append_action(
                issue_id=issue.id,
                action="resolve",
                payload=resolution,
                created_at=now,
            )
            self.mapper.increment_imported_count(issue.batch_id)
            CandidateSuggestionService(self.db).generate([bill_id])
            self.db.commit()
            bill = LedgerMapper(self.db).by_ids([bill_id]).get(bill_id)
            if not bill:
                raise RuntimeError(f"Resolved import issue created missing bill {bill_id}")
            return bill_read_from_vo(bill)
        except Exception:
            self.db.rollback()
            raise

    def dismiss(self, issue_id: int, payload: UndoRequest) -> ImportIssueStatusRead:
        if not payload.reason.strip():
            raise ImportIssueCommandError(422, "请填写为何该记录不属于实际人民币收付")
        self.mapper.begin_immediate()
        try:
            issue = self.mapper.command_issue(issue_id)
            if not issue:
                raise ImportIssueCommandError(404, "错误记录不存在")
            if issue.resolved_at:
                raise ImportIssueCommandError(409, "记录已处理，请刷新")
            resolution = json.dumps({
                "actor": "local-user",
                "action": "not_a_posted_cny_transaction",
                "reason": payload.reason,
            }, ensure_ascii=False)
            now = datetime.now()
            self.mapper.dismiss_issue(
                issue_id=issue.id,
                resolution=resolution,
                resolved_at=now,
            )
            self.mapper.append_action(
                issue_id=issue.id,
                action="dismiss",
                payload=resolution,
                created_at=now,
            )
            self.db.commit()
            return ImportIssueStatusRead(
                id=issue.id,
                status="not_posted",
                reason=payload.reason,
            )
        except Exception:
            self.db.rollback()
            raise

    def reopen(self, issue_id: int, payload: UndoRequest) -> ImportIssueStatusRead:
        self.mapper.begin_immediate()
        try:
            issue = self.mapper.command_issue(issue_id)
            if not issue:
                raise ImportIssueCommandError(404, "错误记录不存在")
            if not issue.resolved_at or issue.bill_id:
                raise ImportIssueCommandError(
                    409,
                    "只能重新核验已标记为非收付、且未产生流水的记录",
                )
            self.mapper.reopen_issue(issue.id)
            self.mapper.append_action(
                issue_id=issue.id,
                action="reopen",
                payload=json.dumps({"reason": payload.reason}, ensure_ascii=False),
                created_at=datetime.now(),
            )
            self.db.commit()
            return ImportIssueStatusRead(id=issue.id, status="pending")
        except Exception:
            self.db.rollback()
            raise

    @staticmethod
    def _details(issues, actions) -> list[ImportIssueRead]:
        actions_by_issue: dict[int, list[ImportIssueActionRead]] = {}
        for action in actions:
            actions_by_issue.setdefault(action.issue_id, []).append(
                ImportIssueActionRead(
                    action=action.action,
                    payload=json.loads(action.payload),
                    actor=action.actor,
                    created_at=action.created_at,
                )
            )
        return [ImportIssueRead(
            id=issue.id,
            filename=issue.filename,
            batch_id=issue.batch_id,
            row_number=issue.source_row_number,
            raw_fields=json.loads(issue.raw_payload),
            error=issue.error,
            bill_id=issue.bill_id,
            resolved_at=issue.resolved_at,
            resolution=issue.resolution,
            history=actions_by_issue.get(issue.id, []),
        ) for issue in issues]

    @staticmethod
    def _assert_batches(issues) -> None:
        missing_batch = next((issue for issue in issues if issue.filename is None), None)
        if missing_batch:
            raise RuntimeError(
                f"Import issue {missing_batch.id} references missing batch {missing_batch.batch_id}"
            )
