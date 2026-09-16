from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from backend.entity import (
    BillFact,
    LedgerEntry,
    LedgerEntryTag,
    ReviewAllocation,
    ReviewCase,
    TargetTag,
    TargetTagView,
)
from backend.schema.target_economic import EconomicPageQuery, EconomicSummaryQuery


class TargetEconomicReadMapper:
    """Explicit-column reads for confirmed Ledger entries."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _flow_columns():
        return (
            LedgerEntry.id,
            LedgerEntry.entry_type,
            LedgerEntry.entry_direction,
            LedgerEntry.amount_value,
            LedgerEntry.amount_scale,
            LedgerEntry.currency_code,
            LedgerEntry.account_code,
            LedgerEntry.counterparty_account_ref,
            LedgerEntry.occurred_time,
        )

    def page(self, query: EconomicPageQuery):
        clauses = self._clauses(query)
        total = self.db.scalar(select(func.count(LedgerEntry.id)).where(*clauses)) or 0
        rows = self.db.execute(select(*self._flow_columns()).where(*clauses).order_by(
            LedgerEntry.occurred_time.desc(),
            LedgerEntry.id.desc(),
        ).offset((query.page - 1) * query.page_size).limit(query.page_size)).mappings().all()
        return rows, total, self.tags([row["id"] for row in rows])

    def detail(self, ledger_id: int):
        flow = self.db.execute(select(*self._flow_columns()).where(
            LedgerEntry.id == ledger_id,
            *self._active_clauses(),
        )).mappings().one_or_none()
        if flow is None:
            return None
        allocations = self.db.execute(select(
            ReviewAllocation.id,
            ReviewAllocation.review_case_id,
            ReviewAllocation.transaction_fact_id,
            ReviewAllocation.ledger_entry_id,
            ReviewAllocation.amount_value,
            ReviewAllocation.amount_scale,
            ReviewAllocation.currency_code,
        ).join(
            ReviewCase,
            ReviewCase.id == ReviewAllocation.review_case_id,
        ).where(
            ReviewAllocation.ledger_entry_id == ledger_id,
            ReviewCase.status == 0,
        ).order_by(ReviewAllocation.id)).mappings().all()
        fact_ids = sorted({row["transaction_fact_id"] for row in allocations})
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
        ).where(
            BillFact.id.in_(fact_ids)
        ).order_by(BillFact.id)).mappings().all() if fact_ids else []
        review_ids = sorted({row["review_case_id"] for row in allocations})
        reviews = self.db.execute(select(
            ReviewCase.id,
            ReviewCase.behavior_type,
            ReviewCase.status,
            ReviewCase.title,
        ).where(
            ReviewCase.id.in_(review_ids),
        ).order_by(ReviewCase.id)).mappings().all() if review_ids else []
        return (
            flow,
            allocations,
            facts,
            reviews,
            self.tags([ledger_id]).get(ledger_id, []),
        )

    def tags(self, ledger_ids: list[int]) -> dict[int, list[dict]]:
        if not ledger_ids:
            return {}
        rows = self.db.execute(select(
            LedgerEntryTag.ledger_id,
            TargetTagView.name.label("view_name"),
            TargetTagView.system_name.label("view_system_name"),
            TargetTag.name.label("tag_name"),
            TargetTag.system_name.label("tag_system_name"),
        ).join(
            TargetTag,
            TargetTag.id == LedgerEntryTag.tag_id,
        ).join(
            TargetTagView,
            TargetTagView.id == TargetTag.view_id,
        ).where(
            LedgerEntryTag.ledger_id.in_(ledger_ids),
            TargetTagView.status == "ACTIVE",
            TargetTag.status == "ACTIVE",
        ).order_by(
            LedgerEntryTag.ledger_id,
            TargetTagView.id,
            TargetTag.id,
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
            clauses.append(
                LedgerEntry.occurred_time >= datetime.combine(query.date_from, time.min)
            )
        if query.date_to:
            clauses.append(
                LedgerEntry.occurred_time <= datetime.combine(query.date_to, time.max)
            )
        return self.db.execute(select(
            LedgerEntry.id,
            LedgerEntry.entry_type,
            LedgerEntry.entry_direction,
            LedgerEntry.amount_value,
            LedgerEntry.amount_scale,
            LedgerEntry.currency_code,
        ).where(*clauses)).mappings().all()

    @staticmethod
    def _clauses(query: EconomicPageQuery) -> list:
        clauses = TargetEconomicReadMapper._active_clauses()
        if query.date_from:
            clauses.append(
                LedgerEntry.occurred_time >= datetime.combine(query.date_from, time.min)
            )
        if query.date_to:
            clauses.append(
                LedgerEntry.occurred_time <= datetime.combine(query.date_to, time.max)
            )
        if query.entry_type:
            clauses.append(LedgerEntry.entry_type.in_(query.entry_type))
        if query.currency_code:
            clauses.append(LedgerEntry.currency_code.in_(query.currency_code))
        if query.q:
            clauses.append(exists(select(ReviewAllocation.id).join(
                ReviewCase,
                ReviewCase.id == ReviewAllocation.review_case_id,
            ).join(
                BillFact,
                BillFact.id == ReviewAllocation.transaction_fact_id,
            ).where(
                ReviewAllocation.ledger_entry_id == LedgerEntry.id,
                ReviewCase.status == 0,
                or_(
                    ReviewCase.title.ilike(f"%{query.q}%"),
                    BillFact.counterparty.ilike(f"%{query.q}%"),
                    BillFact.summary.ilike(f"%{query.q}%"),
                ),
            )))
        return clauses

    @staticmethod
    def _active_clauses() -> list:
        return [
            LedgerEntry.entry_type.in_((0, 1, 2)),
            LedgerEntry.entry_direction.in_((1, 2)),
            LedgerEntry.amount_value > 0,
            LedgerEntry.occurred_time.is_not(None),
            exists(select(ReviewAllocation.id).join(
                ReviewCase,
                ReviewCase.id == ReviewAllocation.review_case_id,
            ).where(
                ReviewAllocation.ledger_entry_id == LedgerEntry.id,
                ReviewCase.status == 0,
            )),
        ]
