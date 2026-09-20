from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.entity import (
    LedgerEntry,
    ReviewAllocation,
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
        "occurred_time": TransactionFact.occurred_time,
        "amount": TransactionFact.amount,
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
        filter_value: TransactionFactFilter,
        sorter: TransactionFactSorter,
    ) -> tuple[list[dict], int]:
        clauses = []
        if filter_value.id:
            clauses.append(TransactionFact.id == filter_value.id)
        if filter_value.cash_direction:
            clauses.append(TransactionFact.cash_direction == filter_value.cash_direction)
        if filter_value.currency_code:
            clauses.append(TransactionFact.currency_code == filter_value.currency_code.upper())
        if filter_value.account_code:
            clauses.append(TransactionFact.account_code == filter_value.account_code)
        if filter_value.occurred_time_start:
            clauses.append(TransactionFact.occurred_time >= filter_value.occurred_time_start)
        if filter_value.occurred_time_end:
            clauses.append(TransactionFact.occurred_time < filter_value.occurred_time_end)

        total = int(self.db.scalar(
            select(func.count(TransactionFact.id)).where(*clauses)
        ) or 0)
        column = self._SORT_COLUMNS[sorter.field]
        order = column.asc() if sorter.order == "asc" else column.desc()
        id_order = TransactionFact.id.asc() if sorter.order == "asc" else TransactionFact.id.desc()
        orders = [order, id_order]
        if sorter.field == "amount" and not filter_value.currency_code:
            orders = [TransactionFact.currency_code.asc(), order, id_order]
        rows = self.db.execute(select(
            *self._fact_columns(),
        ).where(*clauses).order_by(*orders).offset(
            (page - 1) * page_size
        ).limit(page_size)).mappings().all()
        return [dict(row) for row in rows], total

    def by_import_file(self, import_file_id: int) -> list[dict]:
        imported_fact_ids = select(TransactionImportRow.transaction_fact_id).where(
            TransactionImportRow.transaction_import_file_id == import_file_id,
            TransactionImportRow.transaction_fact_id > 0,
        ).distinct()
        rows = self.db.execute(select(
            *self._fact_columns(),
        ).where(
            TransactionFact.id.in_(imported_fact_ids),
        ).order_by(
            TransactionFact.occurred_time.desc(),
            TransactionFact.id.desc(),
        )).mappings().all()
        return [dict(row) for row in rows]

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
            TransactionImportRow.issue_message,
            TransactionImportRow.raw_payload,
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
            (ReviewCase.status == 0).label("active"),
            LedgerEntry.entry_type,
            LedgerEntry.entry_direction,
            LedgerEntry.amount,
            LedgerEntry.currency_code,
            LedgerEntry.account_code,
            LedgerEntry.occurred_time,
        ).join(
            ReviewAllocation,
            ReviewAllocation.ledger_entry_id == LedgerEntry.id,
        ).join(
            ReviewCase,
            ReviewCase.id == ReviewAllocation.review_case_id,
        ).where(
            LedgerEntry.id.in_(economic_ids),
        ).order_by(LedgerEntry.occurred_time.desc(), LedgerEntry.id.desc())).mappings().all()
        return [dict(row) for row in rows]
