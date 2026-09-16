from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, cast, or_, select, text, update
from sqlalchemy.orm import Session

from backend.entity import (
    IMPORT_ROW_STATUS_ACCEPTED,
    IMPORT_ROW_STATUS_INVALID,
    IMPORT_ROW_STATUS_SKIPPED,
    TransactionFact,
    TransactionImportRow,
)


class ImportFactConflictMapper:
    """CRUD adapter for FACT_CONFLICT rows in transaction_import_row."""

    def __init__(self, db: Session):
        self.db = db

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    def rows(self, q: str = "") -> list[dict]:
        clauses = [TransactionImportRow.issue_code == "FACT_CONFLICT"]
        if q:
            pattern = f"%{q}%"
            clauses.append(or_(
                cast(TransactionImportRow.id, String).like(pattern),
                TransactionImportRow.issue_message.ilike(pattern),
                TransactionImportRow.source_reference.ilike(pattern),
            ))
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
        ).where(*clauses)).mappings().all()
        return [dict(row) for row in rows]

    def row(self, conflict_id: int) -> dict | None:
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

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()


CONFLICT_STATUS_CODES = {
    "PENDING": IMPORT_ROW_STATUS_INVALID,
    "REJECTED": IMPORT_ROW_STATUS_SKIPPED,
    "CONFIRMED": IMPORT_ROW_STATUS_ACCEPTED,
}
