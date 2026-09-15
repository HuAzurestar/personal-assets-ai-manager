from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from backend.entity import BillFact, BillRaw, ReviewCase, ReviewCaseBill, ReviewHistory


@dataclass(frozen=True, slots=True)
class ConflictRaw:
    id: int
    bill_id: int
    raw_payload: str
    raw_hash: str
    parse_status: str
    issue_code: str
    issue_message: str


class TargetFactConflictMapper:
    def __init__(self, db: Session):
        self.db = db

    def raw(self, raw_id: int) -> ConflictRaw | None:
        row = self.db.execute(select(
            BillRaw.id,
            BillRaw.bill_id,
            BillRaw.raw_payload,
            BillRaw.raw_hash,
            BillRaw.parse_status,
            BillRaw.issue_code,
            BillRaw.issue_message,
        ).where(BillRaw.id == raw_id)).mappings().one_or_none()
        return ConflictRaw(**row) if row else None

    def fact(self, fact_id: int) -> BillFact | None:
        return self.db.get(BillFact, fact_id)

    def create_fact(self, values: dict[str, object], now: datetime) -> int:
        fact = BillFact(**values, created_time=now, updated_time=now)
        self.db.add(fact)
        self.db.flush()
        return fact.id

    def write_resolution(
        self,
        *,
        case_id: int,
        raw_id: int,
        fact: BillFact,
        result_json: str,
        expected_version: int,
        now: datetime,
    ) -> int:
        version = self._update_case(
            case_id,
            status="CONFIRMED",
            allocation_status="COMPLETE",
            result_json=result_json,
            expected_version=expected_version,
            now=now,
        )
        self.db.execute(update(BillRaw).where(BillRaw.id == raw_id).values(
            bill_id=fact.id,
            parse_status="SUCCESS",
            updated_time=now,
        ))
        self.db.execute(delete(ReviewCaseBill).where(
            ReviewCaseBill.case_id == case_id
        ))
        self.db.add(ReviewCaseBill(
            case_id=case_id,
            bill_id=fact.id,
            role="FACT_ACCEPTED",
            party="",
            amount_value=fact.amount_value,
            amount_scale=fact.amount_scale,
            currency_code=fact.currency_code,
            created_time=now,
            updated_time=now,
        ))
        self.db.flush()
        return version

    def write_state(
        self,
        *,
        case_id: int,
        raw_id: int,
        status: str,
        raw_status: str,
        allocation_status: str,
        result_json: str,
        expected_version: int,
        now: datetime,
    ) -> int:
        version = self._update_case(
            case_id,
            status=status,
            allocation_status=allocation_status,
            result_json=result_json,
            expected_version=expected_version,
            now=now,
        )
        self.db.execute(update(BillRaw).where(BillRaw.id == raw_id).values(
            bill_id=0,
            parse_status=raw_status,
            updated_time=now,
        ))
        self.db.execute(delete(ReviewCaseBill).where(
            ReviewCaseBill.case_id == case_id
        ))
        return version

    def _update_case(
        self,
        case_id: int,
        *,
        status: str,
        allocation_status: str,
        result_json: str,
        expected_version: int,
        now: datetime,
    ) -> int:
        result = self.db.execute(update(ReviewCase).where(
            ReviewCase.id == case_id,
            ReviewCase.version == expected_version,
        ).values(
            status=status,
            allocation_status=allocation_status,
            version=expected_version + 1,
            result_json=result_json,
            updated_time=now,
        ))
        if result.rowcount != 1:
            raise ValueError("conflict Review changed; reload before writing")
        return expected_version + 1

    def latest_dismiss_history_id(self, case_id: int) -> int:
        return self.db.scalar(select(ReviewHistory.id).where(
            ReviewHistory.case_id == case_id,
            ReviewHistory.operation == "DISMISS",
        ).order_by(ReviewHistory.version.desc()).limit(1)) or 0
