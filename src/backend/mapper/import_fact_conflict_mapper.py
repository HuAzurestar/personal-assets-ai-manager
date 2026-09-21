from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, func, select, text, update
from sqlalchemy.orm import Session

from backend.entity import (
    IMPORT_FILE_STATUS_FAILED,
    IMPORT_FILE_STATUS_IMPORTED,
    IMPORT_FILE_STATUS_PARTIAL,
    IMPORT_ROW_STATUS_ACCEPTED,
    IMPORT_ROW_STATUS_INVALID,
    IMPORT_ROW_STATUS_SKIPPED,
    TransactionFact,
    TransactionImportFile,
    TransactionImportRow,
)


class ImportFactConflictMapper:
    """CRUD adapter for FACT_CONFLICT rows in transaction_import_row."""

    def __init__(self, db: Session):
        self.db = db

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    def page(
        self,
        *,
        row_status: int | None,
        page_index: int,
        page_size: int,
    ) -> tuple[list[dict], int]:
        clauses = [TransactionImportRow.issue_code == "FACT_CONFLICT"]
        if row_status is not None:
            clauses.append(TransactionImportRow.row_status == row_status)
        total = int(self.db.scalar(
            select(func.count(TransactionImportRow.id)).where(*clauses)
        ) or 0)
        rows = self.db.execute(select(
            TransactionImportRow.id,
            TransactionImportRow.transaction_fact_id,
            TransactionImportRow.raw_payload,
            TransactionImportRow.raw_hash,
            TransactionImportRow.row_status,
            TransactionImportRow.issue_code,
            TransactionImportRow.issue_message,
            TransactionImportRow.created_time,
            TransactionImportRow.updated_time,
        ).where(*clauses).order_by(
            TransactionImportRow.id.desc()
        ).offset(
            (page_index - 1) * page_size
        ).limit(page_size)).mappings().all()
        return [dict(row) for row in rows], total

    def row(self, conflict_id: int) -> dict | None:
        rows = self.db.execute(select(
            TransactionImportRow.id,
            TransactionImportRow.transaction_fact_id,
            TransactionImportRow.transaction_import_file_id,
            TransactionImportRow.raw_payload,
            TransactionImportRow.raw_hash,
            TransactionImportRow.row_status,
            TransactionImportRow.issue_code,
            TransactionImportRow.issue_message,
            TransactionImportRow.created_time,
            TransactionImportRow.updated_time,
        ).where(
            TransactionImportRow.id == conflict_id,
            TransactionImportRow.issue_code == "FACT_CONFLICT",
        )).mappings().one_or_none()
        return dict(rows) if rows is not None else None

    def fact(self, fact_id: int) -> TransactionFact | None:
        return self.db.get(TransactionFact, fact_id)

    def create_fact(self, values: dict, now: datetime) -> int:
        fact = TransactionFact(**values, created_time=now, updated_time=now)
        self.db.add(fact)
        self.db.flush()
        return fact.id

    def update_status(
        self,
        conflict_id: int,
        *,
        previous_updated_time: datetime,
        row_status: int,
        transaction_fact_id: int,
        now: datetime,
    ) -> bool:
        result = self.db.execute(update(TransactionImportRow).where(
            TransactionImportRow.id == conflict_id,
            TransactionImportRow.updated_time == previous_updated_time,
        ).values(
            row_status=row_status,
            transaction_fact_id=transaction_fact_id,
            updated_time=now,
        ))
        return result.rowcount == 1

    def refresh_file_summary(self, import_file_id: int, now: datetime) -> bool:
        summary = self.db.execute(select(
            func.count(TransactionImportRow.id).label("total_count"),
            func.coalesce(func.sum(case(
                (TransactionImportRow.row_status == IMPORT_ROW_STATUS_ACCEPTED, 1),
                else_=0,
            )), 0).label("success_count"),
            func.coalesce(func.sum(case(
                (TransactionImportRow.row_status == IMPORT_ROW_STATUS_SKIPPED, 1),
                else_=0,
            )), 0).label("skip_count"),
        ).where(
            TransactionImportRow.transaction_import_file_id == import_file_id
        )).mappings().one()
        total_count = int(summary["total_count"])
        success_count = int(summary["success_count"])
        skip_count = int(summary["skip_count"])
        issue_count = total_count - success_count - skip_count
        status = (
            IMPORT_FILE_STATUS_FAILED
            if issue_count and not success_count
            else IMPORT_FILE_STATUS_PARTIAL
            if issue_count
            else IMPORT_FILE_STATUS_IMPORTED
        )
        result = self.db.execute(update(TransactionImportFile).where(
            TransactionImportFile.id == import_file_id
        ).values(
            total_count=total_count,
            success_count=success_count,
            skip_count=skip_count,
            issue_count=issue_count,
            status=status,
            updated_time=now,
        ))
        return result.rowcount == 1

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()


CONFLICT_STATUS_CODES = {
    "PENDING": IMPORT_ROW_STATUS_INVALID,
    "REJECTED": IMPORT_ROW_STATUS_SKIPPED,
    "CONFIRMED": IMPORT_ROW_STATUS_ACCEPTED,
}
