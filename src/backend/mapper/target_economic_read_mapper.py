from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import exists, func, or_, select
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

    _SORT_COLUMNS = {
        "id": LedgerEntry.id,
        "occurred_time": LedgerEntry.occurred_time,
        "amount_value": LedgerEntry.amount_value,
        "projection_version": LedgerEntry.projection_version,
    }

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
            LedgerEntry.projection_version,
            LedgerEntry.occurred_time,
        )

    def page(self, query: EconomicPageQuery):
        clauses = self._clauses(query)
        total = self.db.scalar(select(func.count(LedgerEntry.id)).where(*clauses)) or 0
        column = self._SORT_COLUMNS[query.sort_field]
        order = column.asc() if query.sort_order == "asc" else column.desc()
        id_order = LedgerEntry.id.asc() if query.sort_order == "asc" else LedgerEntry.id.desc()
        rows = self.db.execute(select(*self._flow_columns()).where(*clauses).order_by(
            order, id_order,
        ).offset((query.page - 1) * query.page_size).limit(query.page_size)).mappings().all()
        return rows, total

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
            ReviewCaseBill.economic_id.label("economic_id"),
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
            ReviewCase.review_type,
            ReviewCase.behavior_code,
            ReviewCase.status,
            ReviewCase.version,
            ReviewCase.title,
        ).join(
            ReviewCaseBill,
            ReviewCaseBill.case_id == ReviewCase.id,
        ).where(
            ReviewCaseBill.bill_id.in_(fact_ids),
            or_(
                ReviewCaseBill.economic_id == economic_id,
                ReviewCase.review_type.in_(("ACCOUNT", "TAG")),
            ),
        ).distinct().order_by(ReviewCase.id)).mappings().all() if fact_ids else []
        account_version_rows = self.db.execute(select(
            ReviewCaseBill.bill_id,
            ReviewCase.version,
        ).join(
            ReviewCase,
            ReviewCase.id == ReviewCaseBill.case_id,
        ).where(
            ReviewCaseBill.bill_id.in_(fact_ids),
            ReviewCase.review_type == "ACCOUNT",
        ).order_by(ReviewCaseBill.bill_id, ReviewCase.id)).mappings().all() if fact_ids else []
        account_versions: dict[int, int] = {}
        for row in account_version_rows:
            fact_id = row["bill_id"]
            if fact_id in account_versions:
                raise ValueError(f"fact {fact_id} has multiple ACCOUNT reviews")
            account_versions[fact_id] = row["version"]
        return (
            flow,
            allocations,
            facts,
            reviews,
            self.tags([economic_id]).get(economic_id, []),
            account_versions,
        )

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
            clauses.append(LedgerEntry.occurred_time >= datetime.combine(query.date_from, time.min))
        if query.date_to:
            clauses.append(LedgerEntry.occurred_time <= datetime.combine(query.date_to, time.max))
        rows = self.db.execute(select(
            LedgerEntry.id,
            LedgerEntry.entry_type,
            LedgerEntry.entry_direction,
            LedgerEntry.amount_value,
            LedgerEntry.amount_scale,
            LedgerEntry.currency_code,
        ).where(*clauses)).mappings().all()
        return rows

    @staticmethod
    def _clauses(query: EconomicPageQuery) -> list:
        clauses = TargetEconomicReadMapper._active_clauses()
        if query.date_from:
            clauses.append(LedgerEntry.occurred_time >= datetime.combine(query.date_from, time.min))
        if query.date_to:
            clauses.append(LedgerEntry.occurred_time <= datetime.combine(query.date_to, time.max))
        if query.entry_type:
            clauses.append(LedgerEntry.entry_type.in_(query.entry_type))
        if query.currency_code:
            clauses.append(LedgerEntry.currency_code.in_(query.currency_code))
        if query.cash_direction:
            clauses.append(LedgerEntry.entry_direction == query.cash_direction)
        if query.account_code:
            clauses.append(LedgerEntry.account_code == query.account_code)
        if query.q:
            clauses.append(exists(select(ReviewCaseBill.id).join(
                ReviewCase, ReviewCase.id == ReviewCaseBill.case_id,
            ).join(
                BillFact, BillFact.id == ReviewCaseBill.bill_id,
            ).where(
                ReviewCaseBill.economic_id == LedgerEntry.id,
                ReviewCase.status == "CONFIRMED",
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
            exists(select(ReviewCaseBill.id).join(
                ReviewCase, ReviewCase.id == ReviewCaseBill.case_id,
            ).where(
                ReviewCaseBill.economic_id == LedgerEntry.id,
                ReviewCase.status == "CONFIRMED",
            )),
        ]
