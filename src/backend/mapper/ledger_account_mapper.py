from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from backend.entity import LedgerEntry


class LedgerAccountMapper:
    """Ledger-owned account persistence with optimistic projection locking."""

    def __init__(self, db: Session):
        self.db = db

    def get(self, ledger_id: int) -> dict | None:
        row = self.db.execute(select(
            LedgerEntry.id.label("ledger_id"),
            LedgerEntry.account_code,
            LedgerEntry.updated_time,
        ).where(LedgerEntry.id == ledger_id)).mappings().one_or_none()
        return dict(row) if row is not None else None

    def update(
        self,
        ledger_id: int,
        account_code: str,
        expected_updated_time: datetime,
        now: datetime,
    ) -> bool:
        result = self.db.execute(update(LedgerEntry).where(
            LedgerEntry.id == ledger_id,
            LedgerEntry.updated_time == expected_updated_time,
        ).values(
            account_code=account_code,
            updated_time=now,
        ))
        return result.rowcount == 1

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()
