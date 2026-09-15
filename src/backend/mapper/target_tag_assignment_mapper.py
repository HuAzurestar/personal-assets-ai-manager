from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from backend.entity import (
    BillFact,
    LedgerEntry,
    ReviewCase,
    ReviewCaseBill,
    ReviewHistory,
)


@dataclass(frozen=True, slots=True)
class TagAssignmentFact:
    id: int
    amount_value: int
    amount_scale: int
    currency_code: str


@dataclass(frozen=True, slots=True)
class TagAssignmentTarget:
    ledger_id: int
    facts: tuple[TagAssignmentFact, ...]


@dataclass(frozen=True, slots=True)
class ExistingTagCase:
    id: int
    bill_id: int
    status: str
    version: int
    title: str
    result_json: str
    line_id: int
    amount_value: int
    amount_scale: int
    currency_code: str
    created_time: datetime


@dataclass(frozen=True, slots=True)
class TagHistoryReplay:
    operation: str
    request_json: str


class TargetTagAssignmentMapper:
    """Bounded SQL for one ledger-level tag assignment command."""

    def __init__(self, db: Session):
        self.db = db

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    @staticmethod
    def _fact_links():
        return select(
            ReviewCaseBill.economic_id.label("ledger_id"),
            ReviewCaseBill.bill_id.label("fact_id"),
        ).join(
            ReviewCase,
            ReviewCase.id == ReviewCaseBill.case_id,
        ).where(
            ReviewCase.status == "CONFIRMED",
            ReviewCaseBill.economic_id > 0,
        ).subquery()

    def target(self, ledger_id: int) -> TagAssignmentTarget | None:
        links = self._fact_links()
        rows = self.db.execute(select(
            LedgerEntry.id.label("ledger_id"),
            BillFact.id,
            BillFact.amount_value,
            BillFact.amount_scale,
            BillFact.currency_code,
        ).join(
            links,
            links.c.ledger_id == LedgerEntry.id,
        ).join(
            BillFact,
            BillFact.id == links.c.fact_id,
        ).where(
            LedgerEntry.id == ledger_id,
        ).order_by(BillFact.id)).mappings().all()
        if not rows:
            return None
        return TagAssignmentTarget(
            ledger_id=rows[0]["ledger_id"],
            facts=tuple(TagAssignmentFact(
                id=row["id"],
                amount_value=row["amount_value"],
                amount_scale=row["amount_scale"],
                currency_code=row["currency_code"],
            ) for row in rows),
        )

    def ledger_facts(self, fact_ids: list[int]) -> dict[int, list[int]]:
        if not fact_ids:
            return {}
        links = self._fact_links()
        rows = self.db.execute(select(
            links.c.ledger_id,
            links.c.fact_id,
        ).where(links.c.fact_id.in_(fact_ids)).order_by(
            links.c.ledger_id,
            links.c.fact_id,
        )).mappings().all()
        result: dict[int, list[int]] = {}
        for row in rows:
            result.setdefault(row["ledger_id"], []).append(row["fact_id"])
        return result

    def cases(self, fact_ids: list[int]) -> dict[int, ExistingTagCase]:
        if not fact_ids:
            return {}
        rows = self.db.execute(select(
            ReviewCase.id,
            ReviewCaseBill.bill_id,
            ReviewCase.status,
            ReviewCase.version,
            ReviewCase.title,
            ReviewCase.result_json,
            ReviewCaseBill.id.label("line_id"),
            ReviewCaseBill.amount_value,
            ReviewCaseBill.amount_scale,
            ReviewCaseBill.currency_code,
            ReviewCase.created_time,
        ).join(
            ReviewCaseBill,
            ReviewCaseBill.case_id == ReviewCase.id,
        ).where(
            ReviewCase.review_type == "TAG",
            ReviewCaseBill.bill_id.in_(fact_ids),
        ).order_by(ReviewCaseBill.bill_id, ReviewCase.id)).mappings().all()
        result = {}
        for row in rows:
            fact_id = row["bill_id"]
            if fact_id in result:
                raise ValueError(f"fact {fact_id} has more than one TAG review case")
            result[fact_id] = ExistingTagCase(**row)
        return result

    def idempotency(self, key: str) -> TagHistoryReplay | None:
        row = self.db.execute(select(
            ReviewHistory.operation,
            ReviewHistory.request_json,
        ).where(ReviewHistory.idempotency_key == key)).mappings().one_or_none()
        return TagHistoryReplay(**row) if row else None

    def create_cases(
        self,
        facts: list[TagAssignmentFact],
        *,
        title: str,
        result_json: str,
        version: int,
        now: datetime,
    ) -> dict[int, int]:
        cases = [ReviewCase(
            review_type="TAG",
            behavior_code="TAG",
            status="CONFIRMED",
            allocation_status="COMPLETE",
            version=version,
            title=title,
            result_json=result_json,
            created_time=now,
            updated_time=now,
        ) for _fact in facts]
        self.db.add_all(cases)
        self.db.flush()
        return {fact.id: case.id for fact, case in zip(facts, cases, strict=True)}

    def update_cases(
        self,
        cases: list[ExistingTagCase],
        *,
        title: str,
        result_json: str,
        expected_version: int,
        now: datetime,
    ) -> None:
        if not cases:
            return
        result = self.db.execute(update(ReviewCase).where(
            ReviewCase.id.in_([case.id for case in cases]),
            ReviewCase.version == expected_version,
        ).values(
            status="CONFIRMED",
            allocation_status="COMPLETE",
            version=expected_version + 1,
            title=title,
            result_json=result_json,
            updated_time=now,
        ))
        if result.rowcount != len(cases):
            raise ValueError("tag Review changed; reload before assigning tags")

    def replace_lines(
        self,
        case_by_fact: dict[int, int],
        facts: list[TagAssignmentFact],
        *,
        now: datetime,
    ) -> None:
        case_ids = list(case_by_fact.values())
        self.db.execute(delete(ReviewCaseBill).where(
            ReviewCaseBill.case_id.in_(case_ids)
        ))
        self.db.add_all([ReviewCaseBill(
            case_id=case_by_fact[fact.id],
            bill_id=fact.id,
            role="TAGGED",
            party="",
            amount_value=fact.amount_value,
            amount_scale=fact.amount_scale,
            currency_code=fact.currency_code,
            created_time=now,
            updated_time=now,
        ) for fact in facts])
        self.db.flush()

    def add_histories(self, histories: list[ReviewHistory]) -> None:
        self.db.add_all(histories)

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()
