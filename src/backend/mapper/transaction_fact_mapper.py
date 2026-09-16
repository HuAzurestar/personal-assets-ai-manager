from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from backend.entity import BillFact, BillRaw, ImportFile, LedgerEntry, ReviewCase
from backend.schema.transaction_fact import (
    TransactionFactFilter,
    TransactionFactSorter,
)


ECONOMIC_TYPES = {0: "TRANSACTION", 1: "ACCOUNT_TRANSFER", 2: "CLAIM"}
CASH_DIRECTIONS = {1: "IN", 2: "OUT"}


class TransactionFactMapper:
    """Read-only PO queries for Transaction Fact detail surfaces."""

    _SORT_COLUMNS = {
        "id": BillFact.id,
        "occurred_time": BillFact.occurred_time,
        "amount_value": BillFact.amount_value,
        "created_time": BillFact.created_time,
        "updated_time": BillFact.updated_time,
    }

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _fact_columns():
        return (
            BillFact.id,
            BillFact.fact_key,
            BillFact.occurred_time,
            BillFact.cash_direction,
            BillFact.amount_value,
            BillFact.amount_scale,
            BillFact.currency_code,
            BillFact.account_code,
            BillFact.counterparty,
            BillFact.summary,
            BillFact.created_time,
            BillFact.updated_time,
        )

    def page(
        self,
        *,
        page: int,
        page_size: int,
        q: str,
        filter_value: TransactionFactFilter,
        sorter: TransactionFactSorter,
        import_file_id: int | None = None,
    ) -> tuple[list[dict], int]:
        clauses = []
        if import_file_id is not None:
            imported_fact_ids = select(BillRaw.bill_id).where(
                BillRaw.import_file_id == import_file_id,
                BillRaw.bill_id > 0,
            ).distinct()
            clauses.append(BillFact.id.in_(imported_fact_ids))
        if q:
            pattern = f"%{q}%"
            clauses.append(or_(
                cast(BillFact.id, String).like(pattern),
                BillFact.fact_key.like(pattern),
                BillFact.account_code.like(pattern),
                BillFact.counterparty.like(pattern),
                BillFact.summary.like(pattern),
            ))
        if filter_value.cash_direction:
            clauses.append(BillFact.cash_direction == filter_value.cash_direction)
        if filter_value.currency_code:
            clauses.append(BillFact.currency_code == filter_value.currency_code.upper())
        if filter_value.account_code:
            clauses.append(BillFact.account_code == filter_value.account_code)
        if filter_value.date_from:
            clauses.append(BillFact.occurred_time >= datetime.combine(
                filter_value.date_from,
                time.min,
            ))
        if filter_value.date_to:
            clauses.append(BillFact.occurred_time <= datetime.combine(
                filter_value.date_to,
                time.max,
            ))

        total = int(self.db.scalar(
            select(func.count(BillFact.id)).where(*clauses)
        ) or 0)
        column = self._SORT_COLUMNS[sorter.field]
        order = column.asc() if sorter.order == "asc" else column.desc()
        id_order = BillFact.id.asc() if sorter.order == "asc" else BillFact.id.desc()
        rows = self.db.execute(select(
            *self._fact_columns(),
        ).where(*clauses).order_by(order, id_order).offset(
            (page - 1) * page_size
        ).limit(page_size)).mappings().all()
        return [dict(row) for row in rows], total

    def detail(self, fact_id: int) -> dict | None:
        row = self.db.execute(select(
            *self._fact_columns(),
        ).where(BillFact.id == fact_id)).mappings().one_or_none()
        return dict(row) if row is not None else None

    def import_evidence(self, fact_id: int) -> list[dict]:
        rows = self.db.execute(select(
            BillRaw.id.label("raw_id"),
            BillRaw.import_file_id,
            BillRaw.source_row_number,
            BillRaw.source_reference,
            BillRaw.parse_status,
            BillRaw.issue_code,
            ImportFile.filename,
            ImportFile.source_type,
            ImportFile.institution_code,
            ImportFile.file_format,
            ImportFile.created_time.label("imported_time"),
        ).join(
            ImportFile, ImportFile.id == BillRaw.import_file_id,
        ).where(
            BillRaw.bill_id == fact_id,
        ).order_by(
            ImportFile.created_time.desc(), BillRaw.id.desc(),
        )).mappings().all()
        return [dict(row) for row in rows]

    def reviews(self, review_ids: list[int]) -> list[dict]:
        if not review_ids:
            return []
        rows = self.db.execute(select(
            ReviewCase.id,
            ReviewCase.review_type,
            ReviewCase.behavior_code,
            ReviewCase.status,
            ReviewCase.version,
            ReviewCase.title,
            ReviewCase.created_time,
            ReviewCase.updated_time,
        ).where(
            ReviewCase.id.in_(review_ids),
        ).order_by(ReviewCase.updated_time.desc(), ReviewCase.id.desc())).mappings().all()
        return [dict(row) for row in rows]

    def ledgers(self, economic_ids: list[int]) -> list[dict]:
        if not economic_ids:
            return []
        rows = self.db.execute(select(
            LedgerEntry.id,
            LedgerEntry.entry_type,
            LedgerEntry.entry_direction,
            LedgerEntry.amount_value,
            LedgerEntry.amount_scale,
            LedgerEntry.currency_code,
            LedgerEntry.account_code,
            LedgerEntry.occurred_time,
        ).where(
            LedgerEntry.id.in_(economic_ids),
        ).order_by(LedgerEntry.occurred_time.desc(), LedgerEntry.id.desc())).mappings().all()
        return [{
            **dict(row),
            "economic_type": ECONOMIC_TYPES[row["entry_type"]],
            "cash_direction": CASH_DIRECTIONS[row["entry_direction"]],
        } for row in rows]
