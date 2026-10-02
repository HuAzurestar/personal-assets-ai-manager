from __future__ import annotations

from collections import defaultdict

from sqlalchemy import String, cast, func, or_, select, text
from sqlalchemy.orm import Session

from backend.entity import (
    LedgerEntry,
    ReviewAllocation,
    ReviewCase,
    TransactionFact,
)
from backend.schema.target_review import (
    TargetReviewFactVO,
)


ECONOMIC_TYPES = {0: "TRANSACTION", 1: "ACCOUNT_TRANSFER", 2: "ASSET_LIABILITY", 3: "DUPLICATE"}
ECONOMIC_TYPE_IDS = {value: key for key, value in ECONOMIC_TYPES.items()}


class TargetEconomicMapper:
    """Persistence adapter from the semantic Router contract to the DB schema."""

    def __init__(self, db: Session):
        self.db = db

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    def all_fact_ids(self) -> list[int]:
        return list(self.db.scalars(
            select(TransactionFact.id).order_by(TransactionFact.id)
        ).all())

    def facts(self, fact_ids: list[int]) -> tuple[TargetReviewFactVO, ...]:
        if not fact_ids:
            return ()
        rows = self.db.execute(select(
            TransactionFact.id,
            TransactionFact.occurred_time,
            TransactionFact.cash_direction,
            TransactionFact.amount,
            TransactionFact.currency_code,
            TransactionFact.account_code,
            TransactionFact.counterparty_name,
            TransactionFact.counterparty_account_ref,
            TransactionFact.summary,
            TransactionFact.fact_key,
            TransactionFact.created_time,
            TransactionFact.updated_time,
        ).where(TransactionFact.id.in_(fact_ids))).mappings().all()
        return tuple(TargetReviewFactVO(**self._fact_values(row)) for row in rows)

    def allocations_by_relation(
        self,
        *,
        review_ids: list[int] | None = None,
        fact_ids: list[int] | None = None,
        economic_ids: list[int] | None = None,
    ) -> list[dict]:
        clauses = []
        if review_ids:
            clauses.append(ReviewAllocation.review_case_id.in_(review_ids))
        if fact_ids:
            clauses.append(ReviewAllocation.transaction_fact_id.in_(fact_ids))
        if economic_ids:
            clauses.append(ReviewAllocation.ledger_entry_id.in_(economic_ids))
        if not clauses:
            return []
        rows = self.db.execute(select(
            ReviewAllocation.id,
            ReviewAllocation.review_case_id,
            ReviewAllocation.transaction_fact_id,
            ReviewAllocation.ledger_entry_id,
            ReviewAllocation.amount,
            ReviewAllocation.currency_code,
        ).where(*clauses).order_by(ReviewAllocation.id)).mappings().all()
        return [dict(row) for row in rows]

    def review_page(
        self,
        page: int,
        page_size: int,
        filter_value,
        sorter,
    ) -> tuple[list[dict], int]:
        query = select(
            ReviewCase.id,
            ReviewCase.behavior_type,
            ReviewCase.status,
            ReviewCase.title,
            ReviewCase.created_time,
            ReviewCase.updated_time,
        )
        clauses = []
        if filter_value.id:
            clauses.append(ReviewCase.id == filter_value.id)
        if filter_value.status is not None:
            clauses.append(ReviewCase.status == filter_value.status)
        if filter_value.behavior_type is not None:
            clauses.append(ReviewCase.behavior_type == filter_value.behavior_type)
        if filter_value.created_time_start:
            clauses.append(ReviewCase.created_time >= filter_value.created_time_start)
        if filter_value.created_time_end:
            clauses.append(ReviewCase.created_time < filter_value.created_time_end)
        if filter_value.updated_time_start:
            clauses.append(ReviewCase.updated_time >= filter_value.updated_time_start)
        if filter_value.updated_time_end:
            clauses.append(ReviewCase.updated_time < filter_value.updated_time_end)
        query = query.where(*clauses)
        total = int(self.db.scalar(
            select(func.count()).select_from(query.order_by(None).subquery())
        ) or 0)
        columns = {
            "id": ReviewCase.id,
            "created_time": ReviewCase.created_time,
            "updated_time": ReviewCase.updated_time,
        }
        column = columns[sorter.field]
        order = column.asc() if sorter.order == "asc" else column.desc()
        id_order = (
            ReviewCase.id.asc()
            if sorter.order == "asc"
            else ReviewCase.id.desc()
        )
        rows = self.db.execute(query.order_by(order, id_order).offset(
            (page - 1) * page_size
        ).limit(page_size)).mappings().all()
        return [dict(row) for row in rows], total

    def _fact_candidate_query(self, q: str = "", filter_value=None):
        clauses = [ReviewCase.status == 0, self._system_case_exists()]
        if q:
            pattern = f"%{q}%"
            clauses.append(or_(
                cast(TransactionFact.id, String).like(pattern),
                TransactionFact.account_code.ilike(pattern),
                TransactionFact.counterparty_name.ilike(pattern),
                TransactionFact.summary.ilike(pattern),
            ))
        if filter_value is not None:
            if filter_value.cash_direction:
                clauses.append(
                    TransactionFact.cash_direction == filter_value.cash_direction
                )
            if filter_value.currency_code:
                clauses.append(
                    TransactionFact.currency_code == filter_value.currency_code.upper()
                )
            if filter_value.account_code:
                clauses.append(TransactionFact.account_code == filter_value.account_code)
            if filter_value.occurred_time_start:
                clauses.append(TransactionFact.occurred_time >= filter_value.occurred_time_start)
            if filter_value.occurred_time_end:
                clauses.append(TransactionFact.occurred_time < filter_value.occurred_time_end)
        return select(
            TransactionFact.id,
            TransactionFact.occurred_time,
            TransactionFact.cash_direction,
            TransactionFact.amount,
            TransactionFact.currency_code,
            TransactionFact.account_code,
            TransactionFact.counterparty_name,
            TransactionFact.summary,
            func.sum(ReviewAllocation.amount).label("available_amount"),
        ).join(
            ReviewAllocation,
            ReviewAllocation.transaction_fact_id == TransactionFact.id,
        ).join(
            ReviewCase,
            ReviewCase.id == ReviewAllocation.review_case_id,
        ).join(
            LedgerEntry,
            LedgerEntry.id == ReviewAllocation.ledger_entry_id,
        ).where(*clauses).group_by(TransactionFact.id)

    def fact_candidate_page(
        self,
        page: int,
        page_size: int,
        q: str = "",
        filter_value=None,
        sorter=None,
    ) -> tuple[list[dict], int]:
        query = self._fact_candidate_query(q, filter_value)
        total = int(self.db.scalar(
            select(func.count()).select_from(query.order_by(None).subquery())
        ) or 0)
        sort_field = sorter.field if sorter is not None else "occurred_time"
        sort_order = sorter.order if sorter is not None else "desc"
        columns = {
            "id": TransactionFact.id,
            "occurred_time": TransactionFact.occurred_time,
            "amount": TransactionFact.amount,
            "available_amount": func.sum(ReviewAllocation.amount),
        }
        column = columns[sort_field]
        order = column.asc() if sort_order == "asc" else column.desc()
        id_order = (
            TransactionFact.id.asc() if sort_order == "asc" else TransactionFact.id.desc()
        )
        rows = self.db.execute(query.order_by(order, id_order).offset(
            (page - 1) * page_size
        ).limit(page_size)).mappings().all()
        return [self._fact_values(row) for row in rows], total

    def fact_candidates(self, limit: int) -> list[dict]:
        rows = self.db.execute(
            self._fact_candidate_query().order_by(
                TransactionFact.occurred_time.desc(), TransactionFact.id.desc()
            ).limit(limit)
        ).mappings().all()
        return [self._fact_values(row) for row in rows]

    def default_allocations(self, fact_ids: list[int]) -> list[dict]:
        if not fact_ids:
            return []
        rows = self.db.execute(select(
            ReviewAllocation.id,
            ReviewAllocation.review_case_id,
            ReviewAllocation.transaction_fact_id,
            ReviewAllocation.ledger_entry_id,
            ReviewAllocation.amount,
            ReviewAllocation.currency_code,
        ).join(
            ReviewCase, ReviewCase.id == ReviewAllocation.review_case_id,
        ).where(
            ReviewAllocation.transaction_fact_id.in_(fact_ids),
            ReviewCase.status == 0,
            self._system_case_exists(),
        ).order_by(
            ReviewAllocation.transaction_fact_id, ReviewAllocation.id
        )).mappings().all()
        return [dict(row) for row in rows]

    def fact_coverage(self, fact_ids: list[int]) -> dict[int, int]:
        if not fact_ids:
            return {}
        rows = self.db.execute(select(
            ReviewAllocation.transaction_fact_id,
            func.sum(ReviewAllocation.amount).label("amount"),
        ).join(
            ReviewCase, ReviewCase.id == ReviewAllocation.review_case_id,
        ).join(
            LedgerEntry, LedgerEntry.id == ReviewAllocation.ledger_entry_id,
        ).where(
            ReviewAllocation.transaction_fact_id.in_(fact_ids),
            ReviewCase.status == 0,
        ).group_by(ReviewAllocation.transaction_fact_id)).mappings().all()
        return {
            row["transaction_fact_id"]: int(row["amount"] or 0)
            for row in rows
        }

    def active_economic_facts(self, fact_ids: list[int]) -> dict[int, list[int]]:
        if not fact_ids:
            return {}
        rows = self.db.execute(select(
            ReviewAllocation.ledger_entry_id,
            ReviewAllocation.transaction_fact_id,
        ).join(
            ReviewCase, ReviewCase.id == ReviewAllocation.review_case_id,
        ).where(
            ReviewAllocation.transaction_fact_id.in_(fact_ids),
            ReviewCase.status == 0,
        ).distinct()).mappings().all()
        result = defaultdict(list)
        for row in rows:
            result[row["ledger_entry_id"]].append(row["transaction_fact_id"])
        return dict(result)

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()

    @staticmethod
    def _fact_values(row) -> dict:
        return dict(row)

    @staticmethod
    def _system_case_exists():
        return ReviewCase.behavior_type == 0
