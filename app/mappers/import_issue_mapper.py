from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.errors import ImportIssueCommandError
from app.database import Bill, ImportBatch, ImportIssueAction, ImportRowIssue, LedgerOrigin
from app.schemas.import_issue import (
    ImportIssueActionVO,
    ImportIssueCommandVO,
    ImportIssueSummaryVO,
    ImportIssueVO,
)


class ImportIssueMapper:
    """Set-based read SQL for imported-row issues and their history."""

    def __init__(self, db: Session):
        self.db = db

    def begin_immediate(self) -> None:
        try:
            self.db.execute(text("BEGIN IMMEDIATE"))
        except OperationalError as error:
            self.db.rollback()
            raise ImportIssueCommandError(
                409,
                "Ledger is busy; retry the import review operation",
            ) from error

    def issues(self) -> tuple[ImportIssueVO, ...]:
        rows = self.db.execute(
            select(
                ImportRowIssue.id,
                ImportBatch.filename,
                ImportRowIssue.import_batch_id.label("batch_id"),
                ImportRowIssue.source_row_number,
                ImportRowIssue.raw_payload,
                ImportRowIssue.error,
                ImportRowIssue.bill_id,
                ImportRowIssue.resolved_at,
                ImportRowIssue.resolution,
            )
            .outerjoin(ImportBatch, ImportBatch.id == ImportRowIssue.import_batch_id)
            .order_by(ImportRowIssue.id.desc())
        ).mappings().all()
        return tuple(ImportIssueVO(**row) for row in rows)

    def page(
        self,
        *,
        page: int,
        page_size: int,
    ) -> tuple[int, tuple[ImportIssueSummaryVO, ...]]:
        total = self.db.scalar(select(func.count(ImportRowIssue.id))) or 0
        rows = self.db.execute(
            select(
                ImportRowIssue.id,
                ImportBatch.filename,
                ImportRowIssue.import_batch_id.label("batch_id"),
                ImportRowIssue.source_row_number,
                ImportRowIssue.error,
                ImportRowIssue.bill_id,
                ImportRowIssue.resolved_at,
            )
            .outerjoin(ImportBatch, ImportBatch.id == ImportRowIssue.import_batch_id)
            .order_by(ImportRowIssue.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).mappings().all()
        return total, tuple(ImportIssueSummaryVO(**row) for row in rows)

    def issue(self, issue_id: int) -> ImportIssueVO | None:
        row = self.db.execute(
            select(
                ImportRowIssue.id,
                ImportBatch.filename,
                ImportRowIssue.import_batch_id.label("batch_id"),
                ImportRowIssue.source_row_number,
                ImportRowIssue.raw_payload,
                ImportRowIssue.error,
                ImportRowIssue.bill_id,
                ImportRowIssue.resolved_at,
                ImportRowIssue.resolution,
            )
            .outerjoin(ImportBatch, ImportBatch.id == ImportRowIssue.import_batch_id)
            .where(ImportRowIssue.id == issue_id)
        ).mappings().one_or_none()
        return ImportIssueVO(**row) if row else None

    def actions(self, issue_ids: list[int]) -> tuple[ImportIssueActionVO, ...]:
        if not issue_ids:
            return ()
        rows = self.db.execute(
            select(
                ImportIssueAction.id,
                ImportIssueAction.issue_id,
                ImportIssueAction.action,
                ImportIssueAction.payload,
                ImportIssueAction.actor,
                ImportIssueAction.created_at,
            )
            .where(ImportIssueAction.issue_id.in_(issue_ids))
            .order_by(ImportIssueAction.issue_id, ImportIssueAction.id)
        ).mappings().all()
        return tuple(ImportIssueActionVO(**row) for row in rows)

    def command_issue(self, issue_id: int) -> ImportIssueCommandVO | None:
        row = self.db.execute(
            select(
                ImportRowIssue.id,
                ImportRowIssue.import_batch_id.label("batch_id"),
                ImportBatch.source_type,
                ImportRowIssue.source_row_number,
                ImportRowIssue.raw_payload,
                ImportRowIssue.bill_id,
                ImportRowIssue.resolved_at,
            )
            .outerjoin(ImportBatch, ImportBatch.id == ImportRowIssue.import_batch_id)
            .where(ImportRowIssue.id == issue_id)
        ).mappings().one_or_none()
        return ImportIssueCommandVO(**row) if row else None

    def create_bill(self, values: dict) -> int:
        bill = Bill(**values, category="未分类", tags="")
        self.db.add(bill)
        self.db.flush()
        return bill.id

    def append_origin(
        self,
        *,
        bill_id: int,
        source_type: str,
        source_reference: str,
        import_batch_id: int,
        source_row_number: int,
        raw_payload: str,
    ) -> None:
        self.db.execute(insert(LedgerOrigin).values(
            bill_id=bill_id,
            source_type=source_type,
            source_reference=source_reference,
            import_batch_id=import_batch_id,
            source_row_number=source_row_number,
            raw_payload=raw_payload,
        ))

    def resolve_issue(
        self,
        *,
        issue_id: int,
        bill_id: int,
        resolution: str,
        resolved_at: datetime,
    ) -> None:
        self.db.execute(
            update(ImportRowIssue)
            .where(ImportRowIssue.id == issue_id)
            .values(
                bill_id=bill_id,
                resolved_at=resolved_at,
                resolution=resolution,
            )
        )

    def dismiss_issue(
        self,
        *,
        issue_id: int,
        resolution: str,
        resolved_at: datetime,
    ) -> None:
        self.db.execute(
            update(ImportRowIssue)
            .where(ImportRowIssue.id == issue_id)
            .values(resolved_at=resolved_at, resolution=resolution)
        )

    def reopen_issue(self, issue_id: int) -> None:
        self.db.execute(
            update(ImportRowIssue)
            .where(ImportRowIssue.id == issue_id)
            .values(resolved_at=None)
        )

    def increment_imported_count(self, batch_id: int) -> None:
        self.db.execute(
            update(ImportBatch)
            .where(ImportBatch.id == batch_id)
            .values(imported_count=ImportBatch.imported_count + 1)
        )

    def append_action(
        self,
        *,
        issue_id: int,
        action: str,
        payload: str,
        created_at: datetime,
    ) -> None:
        self.db.execute(insert(ImportIssueAction).values(
            issue_id=issue_id,
            action=action,
            payload=payload,
            actor="local-user",
            created_at=created_at,
        ))
