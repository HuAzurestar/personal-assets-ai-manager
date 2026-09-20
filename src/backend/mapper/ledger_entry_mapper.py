from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from backend.entity import (
    LedgerEntry,
    LedgerEntryTag,
    ReviewAllocation,
    ReviewCase,
    TargetTag,
    TargetTagView,
    TransactionFact,
)
from backend.schema.ledger_entry import (
    LedgerEntryFilter,
    LedgerEntrySorter,
    LedgerEntrySummaryQuery,
)


class LedgerEntryMapper:
    """Explicit-column reads for confirmed Ledger entries."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _flow_columns():
        return (
            LedgerEntry.id,
            LedgerEntry.entry_type,
            LedgerEntry.entry_direction,
            LedgerEntry.amount,
            LedgerEntry.currency_code,
            LedgerEntry.account_code,
            LedgerEntry.counterparty_account_ref,
            LedgerEntry.occurred_time,
            LedgerEntry.created_time,
            LedgerEntry.updated_time,
        )

    def page(
        self,
        *,
        page: int,
        page_size: int,
        filter_value: LedgerEntryFilter,
        sorter: LedgerEntrySorter,
    ):
        clauses = self._clauses(filter_value)
        total = self.db.scalar(select(func.count(LedgerEntry.id)).where(*clauses)) or 0
        sort_columns = {
            "occurred_time": LedgerEntry.occurred_time,
            "amount": LedgerEntry.amount,
        }
        column = sort_columns[sorter.field]
        order = column.asc() if sorter.order == "asc" else column.desc()
        id_order = LedgerEntry.id.asc() if sorter.order == "asc" else LedgerEntry.id.desc()
        orders = [order, id_order]
        if sorter.field == "amount" and not filter_value.currency_code:
            orders = [LedgerEntry.currency_code.asc(), order, id_order]
        rows = self.db.execute(select(
            *self._flow_columns(),
            (ReviewCase.status == 0).label("active"),
            TransactionFact.summary.label("summary"),
            ReviewCase.behavior_type.label("review_behavior_type"),
        ).join(
            ReviewAllocation,
            ReviewAllocation.ledger_entry_id == LedgerEntry.id,
        ).join(
            TransactionFact,
            TransactionFact.id == ReviewAllocation.transaction_fact_id,
        ).join(
            ReviewCase,
            ReviewCase.id == ReviewAllocation.review_case_id,
        ).where(*clauses).order_by(
            *orders,
        ).offset((page - 1) * page_size).limit(page_size)).mappings().all()
        return rows, total

    def detail(self, ledger_id: int):
        flow = self.db.execute(select(*self._flow_columns()).where(
            LedgerEntry.id == ledger_id,
        )).mappings().one_or_none()
        if flow is None:
            return None
        allocations = self.db.execute(select(
            ReviewAllocation.id,
            ReviewAllocation.review_case_id,
            ReviewAllocation.transaction_fact_id,
            ReviewAllocation.ledger_entry_id,
            ReviewAllocation.amount,
            ReviewAllocation.currency_code,
        ).where(
            ReviewAllocation.ledger_entry_id == ledger_id,
        ).order_by(ReviewAllocation.id)).mappings().all()
        fact_ids = sorted({row["transaction_fact_id"] for row in allocations})
        facts = self.db.execute(select(
            TransactionFact.id,
            TransactionFact.occurred_time,
            TransactionFact.cash_direction,
            TransactionFact.amount,
            TransactionFact.currency_code,
            TransactionFact.account_code,
            TransactionFact.counterparty_name,
            TransactionFact.counterparty_account_ref,
            TransactionFact.summary,
        ).where(
            TransactionFact.id.in_(fact_ids)
        ).order_by(TransactionFact.id)).mappings().all() if fact_ids else []
        review_ids = sorted({row["review_case_id"] for row in allocations})
        reviews = self.db.execute(select(
            ReviewCase.id,
            ReviewCase.behavior_type,
            ReviewCase.status,
            ReviewCase.title,
            ReviewCase.created_time,
            ReviewCase.updated_time,
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

    def summary(self, query: LedgerEntrySummaryQuery):
        clauses = self._active_clauses()
        if query.occurred_time_start:
            clauses.append(
                LedgerEntry.occurred_time >= query.occurred_time_start
            )
        if query.occurred_time_end:
            clauses.append(
                LedgerEntry.occurred_time < query.occurred_time_end
            )
        return self.db.execute(select(
            LedgerEntry.id,
            LedgerEntry.entry_type,
            LedgerEntry.entry_direction,
            LedgerEntry.amount,
            LedgerEntry.currency_code,
            LedgerEntry.occurred_time,
        ).where(*clauses)).mappings().all()

    @staticmethod
    def _clauses(filter_value: LedgerEntryFilter) -> list:
        clauses = []
        if filter_value.id:
            clauses.append(LedgerEntry.id == filter_value.id)
        if filter_value.occurred_time_start:
            clauses.append(LedgerEntry.occurred_time >= filter_value.occurred_time_start)
        if filter_value.occurred_time_end:
            clauses.append(LedgerEntry.occurred_time < filter_value.occurred_time_end)
        if filter_value.entry_type is not None:
            clauses.append(LedgerEntry.entry_type == filter_value.entry_type)
        if filter_value.currency_code:
            clauses.append(LedgerEntry.currency_code == filter_value.currency_code)
        if filter_value.entry_direction is not None:
            clauses.append(LedgerEntry.entry_direction == filter_value.entry_direction)
        if filter_value.account_code:
            clauses.append(LedgerEntry.account_code == filter_value.account_code)
        if filter_value.active is not None:
            clauses.append(exists(select(ReviewAllocation.id).join(
                ReviewCase,
                ReviewCase.id == ReviewAllocation.review_case_id,
            ).where(
                ReviewAllocation.ledger_entry_id == LedgerEntry.id,
                ReviewCase.status == (0 if filter_value.active else 1),
            )))
        return clauses

    @staticmethod
    def _active_clauses() -> list:
        return [
            LedgerEntry.entry_type.in_((0, 1, 2)),
            LedgerEntry.entry_direction.in_((1, 2)),
            LedgerEntry.amount > 0,
            LedgerEntry.occurred_time.is_not(None),
            exists(select(ReviewAllocation.id).join(
                ReviewCase,
                ReviewCase.id == ReviewAllocation.review_case_id,
            ).where(
                ReviewAllocation.ledger_entry_id == LedgerEntry.id,
                ReviewCase.status == 0,
            )),
        ]
