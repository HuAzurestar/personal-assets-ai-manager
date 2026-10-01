from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.error import TargetEconomicError
from backend.entity.base import utc_now
from backend.mapper.ledger_account_mapper import LedgerAccountMapper
from backend.schema.ledger_account import LedgerAccountRead, LedgerAccountUpdateRequest


class LedgerAccountService:
    """Read and update the account owned by one Ledger entry."""

    def __init__(self, db: Session):
        self.mapper = LedgerAccountMapper(db)

    def get(self, ledger_id: int) -> LedgerAccountRead:
        row = self.mapper.get(ledger_id)
        if row is None:
            raise TargetEconomicError(404, f"ledger {ledger_id} not found")
        return LedgerAccountRead(**row)

    def update(self, ledger_id, payload):
        raise TargetEconomicError(410, "published Ledger account is immutable; use replacement Review", code="REVIEW_WRITE_RETIRED")
