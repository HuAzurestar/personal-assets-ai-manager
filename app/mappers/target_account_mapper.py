from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models.target import (
    BillFact,
    LedgerEntry,
    LedgerEntrySource,
    ReviewCase,
    ReviewCaseBill,
    ReviewHistory,
)
from app.schemas.target_review import FINANCIAL_REVIEW_TYPES


@dataclass(frozen=True, slots=True)
class AccountTarget:
    fact_id: int
    ledger_id: int
    projection_version: int
    amount_value: int
    amount_scale: int
    currency_code: str
    counterparty: str


class TargetAccountMapper:
    """Explicit SQL for Fact-specific ACCOUNT Review commands."""

    def __init__(self, db: Session):
        self.db = db

    def target(self, fact_id: int) -> AccountTarget | None:
        row = self.db.execute(select(
            BillFact.id.label("fact_id"),
            LedgerEntrySource.ledger_id,
            LedgerEntry.projection_version,
            BillFact.amount_value,
            BillFact.amount_scale,
            BillFact.currency_code,
            BillFact.counterparty,
        ).join(
            LedgerEntrySource,
            (LedgerEntrySource.source_kind == "BILL_FACT")
            & (LedgerEntrySource.source_id == BillFact.id),
        ).join(
            LedgerEntry,
            LedgerEntry.id == LedgerEntrySource.ledger_id,
        ).where(BillFact.id == fact_id)).mappings().one_or_none()
        return AccountTarget(**row) if row else None

    def account_case_id(self, fact_id: int) -> int:
        ids = self.db.scalars(select(ReviewCase.id).join(
            ReviewCaseBill,
            ReviewCaseBill.case_id == ReviewCase.id,
        ).where(
            ReviewCase.review_type == "ACCOUNT",
            ReviewCaseBill.bill_id == fact_id,
        ).order_by(ReviewCase.id)).all()
        if len(ids) > 1:
            raise ValueError(f"fact {fact_id} has more than one ACCOUNT review case")
        return ids[0] if ids else 0

    def financial_case_id(self, ledger_id: int) -> int:
        ids = self.db.scalars(select(ReviewCase.id).join(
            LedgerEntrySource,
            (LedgerEntrySource.source_kind == "REVIEW_CASE")
            & (LedgerEntrySource.source_id == ReviewCase.id),
        ).where(
            LedgerEntrySource.ledger_id == ledger_id,
            ReviewCase.status == "CONFIRMED",
            ReviewCase.review_type.in_(FINANCIAL_REVIEW_TYPES),
        )).all()
        if len(ids) > 1:
            raise ValueError(f"ledger {ledger_id} has multiple financial Review sources")
        return ids[0] if ids else 0

    def create_case(
        self,
        target: AccountTarget,
        result_json: str,
        now: datetime,
    ) -> int:
        case = ReviewCase(
            review_type="ACCOUNT",
            status="CONFIRMED",
            allocation_status="COMPLETE",
            version=1,
            title=f"Account: {target.counterparty}",
            result_json=result_json,
            created_time=now,
            updated_time=now,
        )
        self.db.add(case)
        self.db.flush()
        self._add_line(case.id, target, now)
        return case.id

    def update_case(
        self,
        case_id: int,
        target: AccountTarget,
        result_json: str,
        expected_version: int,
        now: datetime,
    ) -> int:
        result = self.db.execute(update(ReviewCase).where(
            ReviewCase.id == case_id,
            ReviewCase.version == expected_version,
        ).values(
            status="CONFIRMED",
            allocation_status="COMPLETE",
            version=expected_version + 1,
            title=f"Account: {target.counterparty}",
            result_json=result_json,
            updated_time=now,
        ))
        if result.rowcount != 1:
            raise ValueError("account Review changed; reload before updating")
        self.db.execute(delete(ReviewCaseBill).where(
            ReviewCaseBill.case_id == case_id
        ))
        self._add_line(case_id, target, now)
        return expected_version + 1

    def _add_line(self, case_id: int, target: AccountTarget, now: datetime) -> None:
        self.db.add(ReviewCaseBill(
            case_id=case_id,
            bill_id=target.fact_id,
            role="ACCOUNT",
            party="",
            amount_value=target.amount_value,
            amount_scale=target.amount_scale,
            currency_code=target.currency_code,
            created_time=now,
            updated_time=now,
        ))
        self.db.flush()

    def latest_history_id(self, case_id: int, operations: tuple[str, ...]) -> int:
        return self.db.scalar(select(ReviewHistory.id).where(
            ReviewHistory.case_id == case_id,
            ReviewHistory.operation.in_(operations),
        ).order_by(ReviewHistory.version.desc()).limit(1)) or 0
