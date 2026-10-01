"""Bounded public source reads; private evidence only in one-row detail."""
from sqlalchemy import select, or_, and_
from backend.core.import_public_text import masked_reference, public_issue
from backend.entity import TransactionImportFile, TransactionImportRow, TransactionFact, ReviewAllocation, ReviewCase, LedgerEntry
from backend.mapper.review_command_mapper import ReviewCommandMapper
from backend.mapper.bounded_query_mapper import page_rows, scan_rows


def row_po(row):
    return row | dict(source_reference=masked_reference(row["source_reference"]), issue_message=public_issue(row["issue_code"]))


class ImportSourceMapper(ReviewCommandMapper):
    def broken_file_sources(self, file_id):
        row, fact = TransactionImportRow, TransactionFact
        return self.db.scalar(select(row.id).outerjoin(fact, fact.id == row.transaction_fact_id).where(
            row.transaction_import_file_id == file_id, or_(row.source_row_number <= 0,
            row.row_status.not_in((0, 1, 2, 3)), and_(row.row_status == 1, or_(row.transaction_fact_id <= 0, fact.id.is_(None))),
            and_(row.row_status != 1, row.transaction_fact_id != 0))).limit(1)) is not None

    def statement(self, *, rows=False):
        if not rows:
            return select(TransactionImportFile.__table__)
        return select(*[column for column in TransactionImportRow.__table__.columns
                        if column.name not in {"raw_payload", "transaction_fact_id"}],
                      TransactionImportRow.transaction_fact_id.label("transaction_id"))

    def query(self, request, *, file_id=None, search=False):
        entity = TransactionImportRow if file_id is not None else TransactionImportFile
        columns = {column.name: getattr(entity, column.name) for column in entity.__table__.columns}
        rows = file_id is not None
        condition = entity.transaction_import_file_id == file_id if rows else True
        default = (("source_row_number", "asc"),) if rows else (("id", "desc"),)
        statement = self.statement(rows=rows)
        if search:
            return scan_rows(self.db, statement, request, columns, scope=dict(resource="source_row" if rows else "source_file",
                file_id=file_id), condition=condition, default=default, project=row_po if rows else None)
        items, total = page_rows(self.db, statement, request, columns, condition=condition, default=default)
        return dict(items=[row_po(row) for row in items] if rows else items, total=total,
                    page_index=request.page_index, page_size=request.page_size)

    def relation_rows(self, file_id, row_ids):
        return [dict(row) for row in self.db.execute(select(
            TransactionImportRow.id.label("row_id"), TransactionImportRow.source_row_number,
            TransactionImportRow.transaction_fact_id.label("transaction_id"), ReviewAllocation.id.label("allocation_id"),
            ReviewAllocation.review_id, LedgerEntry.id.label("ledger_id"), ReviewCase.status.label("review_status"),
            LedgerEntry.account_ref_id, ReviewAllocation.cash_amount, ReviewAllocation.cash_currency_code
        ).outerjoin(ReviewAllocation, ReviewAllocation.transaction_id == TransactionImportRow.transaction_fact_id)
         .outerjoin(ReviewCase, ReviewCase.id == ReviewAllocation.review_id)
         .outerjoin(LedgerEntry, LedgerEntry.id == ReviewAllocation.ledger_id)
         .where(TransactionImportRow.transaction_import_file_id == file_id, TransactionImportRow.id.in_(row_ids))
         .order_by(TransactionImportRow.source_row_number, TransactionImportRow.id, ReviewAllocation.id).limit(4001)).mappings()]
