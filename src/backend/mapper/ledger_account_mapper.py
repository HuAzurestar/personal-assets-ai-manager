from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from backend.entity import LedgerEntry


class LedgerAccountMapper:
    """Ledger-owned account persistence for serialized local writes."""

    def __init__(self, db: Session):
        self.db = db

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    def get(self, ledger_id: int) -> dict | None:
        row = self.db.execute(select(
            LedgerEntry.id.label("ledger_id"),
            LedgerEntry.account_code,
        ).where(LedgerEntry.id == ledger_id)).mappings().one_or_none()
        return dict(row) if row is not None else None

    def update(
        self,
        ledger_id: int,
        account_code: str,
        now: datetime,
    ) -> None:
        result = self.db.execute(update(LedgerEntry).where(
            LedgerEntry.id == ledger_id,
        ).values(
            account_code=account_code,
            updated_time=now,
        ))
        if result.rowcount != 1:
            raise ValueError(f"ledger {ledger_id} not found")

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()
