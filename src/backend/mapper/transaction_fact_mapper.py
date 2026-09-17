from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from backend.entity import (
    LedgerEntry,
    ReviewCase,
    TransactionFact,
    TransactionImportFile,
    TransactionImportRow,
)
from backend.schema.transaction_fact import (
    TransactionFactFilter,
    TransactionFactSorter,
)


class TransactionFactMapper:
    """Read-only PO queries for Transaction Fact detail surfaces."""

    _SORT_COLUMNS = {
        "id": TransactionFact.id,
        "occurred_time": TransactionFact.occurred_time,
        "amount": TransactionFact.amount,
        "created_time": TransactionFact.created_time,
        "updated_time": TransactionFact.updated_time,
    }

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _fact_columns():
        return (
            TransactionFact.id,
            TransactionFact.fact_key,
            TransactionFact.occurred_time,
            TransactionFact.cash_direction,
            TransactionFact.amount,
            TransactionFact.currency_code,
            TransactionFact.account_code,
            TransactionFact.counterparty_name,
            TransactionFact.counterparty_account_ref,
            TransactionFact.summary,
            TransactionFact.created_time,
            TransactionFact.updated_time,
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
            imported_fact_ids = select(TransactionImportRow.transaction_fact_id).where(
                TransactionImportRow.transaction_import_file_id == import_file_id,
                TransactionImportRow.transaction_fact_id > 0,
            ).distinct()
            clauses.append(TransactionFact.id.in_(imported_fact_ids))
        if q:
            pattern = f"%{q}%"
            clauses.append(or_(
                cast(TransactionFact.id, String).like(pattern),
                TransactionFact.fact_key.like(pattern),
                TransactionFact.account_code.like(pattern),
                TransactionFact.counterparty_name.like(pattern),
                TransactionFact.summary.like(pattern),
            ))
        if filter_value.cash_direction:
            clauses.append(TransactionFact.cash_direction == filter_value.cash_direction)
        if filter_value.currency_code:
            clauses.append(TransactionFact.currency_code == filter_value.currency_code.upper())
        if filter_value.account_code:
            clauses.append(TransactionFact.account_code == filter_value.account_code)
        if filter_value.date_from:
            clauses.append(TransactionFact.occurred_time >= datetime.combine(
                filter_value.date_from,
                time.min,
            ))
        if filter_value.date_to:
            clauses.append(TransactionFact.occurred_time <= datetime.combine(
                filter_value.date_to,
                time.max,
            ))

        total = int(self.db.scalar(
            select(func.count(TransactionFact.id)).where(*clauses)
        ) or 0)
        column = self._SORT_COLUMNS[sorter.field]
        order = column.asc() if sorter.order == "asc" else column.desc()
        id_order = TransactionFact.id.asc() if sorter.order == "asc" else TransactionFact.id.desc()
        rows = self.db.execute(select(
            *self._fact_columns(),
        ).where(*clauses).order_by(order, id_order).offset(
            (page - 1) * page_size
        ).limit(page_size)).mappings().all()
        return [dict(row) for row in rows], total

    def detail(self, fact_id: int) -> dict | None:
        row = self.db.execute(select(
            *self._fact_columns(),
        ).where(TransactionFact.id == fact_id)).mappings().one_or_none()
        return dict(row) if row is not None else None

    def import_evidence(self, fact_id: int) -> list[dict]:
        rows = self.db.execute(select(
            TransactionImportRow.id,
            TransactionImportRow.transaction_import_file_id,
            TransactionImportRow.transaction_fact_id,
            TransactionImportRow.source_row_number,
            TransactionImportRow.source_reference,
            TransactionImportRow.row_status,
            TransactionImportRow.issue_code,
            TransactionImportFile.filename,
            TransactionImportFile.source_type,
            TransactionImportFile.file_format,
            TransactionImportFile.created_time.label("imported_time"),
        ).join(
            TransactionImportFile,
            TransactionImportFile.id == TransactionImportRow.transaction_import_file_id,
        ).where(
            TransactionImportRow.transaction_fact_id == fact_id,
        ).order_by(
            TransactionImportFile.created_time.desc(), TransactionImportRow.id.desc(),
        )).mappings().all()
        return [dict(row) for row in rows]

    def reviews(self, review_ids: list[int]) -> list[dict]:
        if not review_ids:
            return []
        rows = self.db.execute(select(
            ReviewCase.id,
            ReviewCase.behavior_type,
            ReviewCase.status,
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
            LedgerEntry.amount,
            LedgerEntry.currency_code,
            LedgerEntry.account_code,
            LedgerEntry.occurred_time,
        ).where(
            LedgerEntry.id.in_(economic_ids),
        ).order_by(LedgerEntry.occurred_time.desc(), LedgerEntry.id.desc())).mappings().all()
        return [dict(row) for row in rows]
