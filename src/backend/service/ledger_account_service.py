from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.error import TargetEconomicError
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

    def update(
        self,
        ledger_id: int,
        payload: LedgerAccountUpdateRequest,
    ) -> LedgerAccountRead:
        account_code = payload.account_code.strip()
        if not account_code or account_code == "MULTIPLE":
            raise TargetEconomicError(422, "account_code must identify one real account")
        try:
            self.mapper.begin_write()
            current = self.mapper.get(ledger_id)
            if current is None:
                raise TargetEconomicError(404, f"ledger {ledger_id} not found")
            self.mapper.update(ledger_id, account_code, datetime.now())
            self.mapper.commit()
            return self.get(ledger_id)
        except TargetEconomicError:
            self.mapper.rollback()
            raise
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise TargetEconomicError(409, "Ledger account update conflict") from error
        except Exception:
            self.mapper.rollback()
            raise
