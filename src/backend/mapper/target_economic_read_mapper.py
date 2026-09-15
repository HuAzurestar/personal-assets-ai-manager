from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from backend.entity import (
    BillFact,
    LedgerEntry,
    LedgerEntryTag,
    ReviewCase,
    ReviewCaseBill,
    TargetTag,
    TargetTagView,
)
from backend.schema.target_economic import EconomicPageQuery, EconomicSummaryQuery


class TargetEconomicReadMapper:
    """Explicit-column reads for the active Economic projection."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _flow_columns():
        return (
            LedgerEntry.id,
            LedgerEntry.economic_type,
            LedgerEntry.cash_direction,
            LedgerEntry.amount_value,
            LedgerEntry.amount_scale,
            LedgerEntry.currency_code,
            LedgerEntry.title,
            LedgerEntry.start_time,
            LedgerEntry.end_time,
            LedgerEntry.claim_key,
            LedgerEntry.claim_side,
            LedgerEntry.reversal_of_id,
            LedgerEntry.projection_version,
        )

    def page(self, query: EconomicPageQuery):
        clauses = self._clauses(query)
        total = self.db.scalar(select(func.count(LedgerEntry.id)).where(*clauses)) or 0
        rows = self.db.execute(select(*self._flow_columns()).where(*clauses).order_by(
            LedgerEntry.start_time.desc(), LedgerEntry.id.desc(),
        ).offset((query.page - 1) * query.page_size).limit(query.page_size)).mappings().all()
        return rows, total, self.tags([row["id"] for row in rows])

    def detail(self, economic_id: int):
        flow = self.db.execute(select(*self._flow_columns()).where(
            LedgerEntry.id == economic_id,
            *self._active_clauses(),
        )).mappings().one_or_none()
        if flow is None:
            return None
        allocations = self.db.execute(select(
            ReviewCaseBill.id,
            ReviewCaseBill.case_id.label("review_id"),
            ReviewCaseBill.bill_id.label("fact_id"),
            ReviewCaseBill.economic_id,
            ReviewCaseBill.role,
            ReviewCaseBill.amount_value,
            ReviewCaseBill.amount_scale,
            ReviewCaseBill.currency_code,
        ).join(
            ReviewCase, ReviewCase.id == ReviewCaseBill.case_id,
        ).where(
            ReviewCaseBill.economic_id == economic_id,
            ReviewCase.status == "CONFIRMED",
        ).order_by(ReviewCaseBill.id)).mappings().all()
        fact_ids = sorted({row["fact_id"] for row in allocations})
        review_ids = sorted({row["review_id"] for row in allocations})
        facts = self.db.execute(select(
            BillFact.id,
            BillFact.occurred_time,
            BillFact.cash_direction,
            BillFact.amount_value,
            BillFact.amount_scale,
            BillFact.currency_code,
            BillFact.account_code,
            BillFact.counterparty,
            BillFact.summary,
        ).where(BillFact.id.in_(fact_ids)).order_by(BillFact.id)).mappings().all() if fact_ids else []
        reviews = self.db.execute(select(
            ReviewCase.id,
            ReviewCase.behavior_code,
            ReviewCase.status,
            ReviewCase.version,
            ReviewCase.title,
        ).where(ReviewCase.id.in_(review_ids)).order_by(ReviewCase.id)).mappings().all() if review_ids else []
        return flow, allocations, facts, reviews, self.tags([economic_id]).get(economic_id, [])

    def tags(self, economic_ids: list[int]) -> dict[int, list[dict]]:
        if not economic_ids:
            return {}
        rows = self.db.execute(select(
            LedgerEntryTag.ledger_id,
            TargetTagView.name.label("view_name"),
            TargetTagView.system_name.label("view_system_name"),
            TargetTag.name.label("tag_name"),
            TargetTag.system_name.label("tag_system_name"),
        ).join(
            TargetTag, TargetTag.id == LedgerEntryTag.tag_id,
        ).join(
            TargetTagView, TargetTagView.id == TargetTag.view_id,
        ).where(
            LedgerEntryTag.ledger_id.in_(economic_ids),
            TargetTagView.status == "ACTIVE",
            TargetTag.status == "ACTIVE",
        ).order_by(
            LedgerEntryTag.ledger_id, TargetTagView.id, TargetTag.id,
        )).mappings().all()
        result: dict[int, list[dict]] = {}
        for row in rows:
            result.setdefault(row["ledger_id"], []).append({
                key: value for key, value in row.items() if key != "ledger_id"
            })
        return result

    def summary(self, query: EconomicSummaryQuery):
        clauses = self._active_clauses()
        if query.date_from:
            clauses.append(LedgerEntry.start_time >= datetime.combine(query.date_from, time.min))
        if query.date_to:
            clauses.append(LedgerEntry.start_time <= datetime.combine(query.date_to, time.max))
        rows = self.db.execute(select(
            LedgerEntry.id,
            LedgerEntry.economic_type,
            LedgerEntry.cash_direction,
            LedgerEntry.amount_value,
            LedgerEntry.amount_scale,
            LedgerEntry.currency_code,
            LedgerEntry.claim_side,
            LedgerEntry.reversal_of_id,
        ).where(*clauses)).mappings().all()
        return rows

    @staticmethod
    def _clauses(query: EconomicPageQuery) -> list:
        clauses = TargetEconomicReadMapper._active_clauses()
        if query.date_from:
            clauses.append(LedgerEntry.start_time >= datetime.combine(query.date_from, time.min))
        if query.date_to:
            clauses.append(LedgerEntry.start_time <= datetime.combine(query.date_to, time.max))
        if query.economic_type:
            clauses.append(LedgerEntry.economic_type.in_(query.economic_type))
        if query.currency_code:
            clauses.append(LedgerEntry.currency_code.in_(query.currency_code))
        if query.q:
            clauses.append(LedgerEntry.title.ilike(f"%{query.q}%"))
        return clauses

    @staticmethod
    def _active_clauses() -> list:
        # Existing databases can still contain v1 aggregate rows.  They have
        # no V2 amount/direction and must never leak into the Economic API.
        return [
            LedgerEntry.status == "ACTIVE",
            LedgerEntry.economic_type.in_(("TRANSACTION", "ACCOUNT_TRANSFER", "CLAIM")),
            LedgerEntry.cash_direction.in_(("IN", "OUT")),
            LedgerEntry.amount_value > 0,
        ]
