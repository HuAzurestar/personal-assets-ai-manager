"""Explicit bounded Fact/source/first-allocation reads in one snapshot."""
from sqlalchemy import case, or_, select
from backend.entity import (TransactionFact, TransactionImportRow, TransactionImportFile,
    ReviewAllocation, ReviewCase, LedgerEntry)
from backend.error import TargetEconomicError
from backend.mapper.bounded_query_mapper import page_rows, scan_rows
from backend.mapper.candidate_mapper import CandidateMapper
from backend.schema.list_query import SorterExpression


class FactReadMapper:
    def __init__(self, db):
        self.db = db
        f = TransactionFact
        direction = case((f.cash_direction == 1, "IN"), else_="OUT")
        signed = case((f.cash_direction == 1, f.amount), else_=-f.amount)
        self.columns = dict(id=f.id, occurred_time=f.occurred_time, cash_direction=direction, amount=f.amount,
            signed_amount=signed, currency_code=f.currency_code, account_code=f.account_code)
        candidate_columns = CandidateMapper(db).columns
        self.columns.update({key: candidate_columns[key] for key in ("account_ref_id", "account_id", "party_id")})
        self.statement = select(*[column for column in f.__table__.columns if column.name != "fact_key"], signed.label("signed_amount"))

    @staticmethod
    def grouped_request(request):
        if not any(item.key in ("amount", "signed_amount") for item in request.sorter):
            return request
        currency = next((item for item in request.sorter if item.key == "currency_code"), SorterExpression(key="currency_code",direction="asc"))
        return request.model_copy(update=dict(sorter=[currency] + [item for item in request.sorter if item.key != "currency_code"]))

    def page(self, request):
        return page_rows(self.db, self.statement, self.grouped_request(request), self.columns,
            default=(("occurred_time", "desc"), ("id", "asc")))

    def search(self, request, project):
        return scan_rows(self.db, self.statement, self.grouped_request(request), self.columns,
            scope="local:ledger-v1:fact", default=(("occurred_time", "desc"), ("id", "asc")), project=project)

    def get(self, fact_id):
        row = self.db.execute(select(*TransactionFact.__table__.columns).where(TransactionFact.id == fact_id)).mappings().one_or_none()
        return dict(row) if row is not None else None

    def source_guard(self, fact_id):
        r, f = TransactionImportRow, TransactionImportFile
        if self.db.scalar(select(r.id).outerjoin(f, f.id == r.transaction_import_file_id).where(r.transaction_fact_id == fact_id,
            or_(f.id.is_(None), r.row_status != 1, r.source_row_number <= 0)).limit(1)) is not None:
            raise TargetEconomicError(409, "Fact source relation is damaged", code="RELATION_BROKEN")

    @staticmethod
    def source_statement(fact_id):
        r, f = TransactionImportRow, TransactionImportFile
        return select(r.id, r.transaction_import_file_id.label("source_file_id"), r.transaction_fact_id.label("transaction_id"),
            r.source_row_number, r.source_reference, r.row_status, r.issue_code, f.filename, f.source_type, f.file_format,
            f.created_time.label("imported_time")).select_from(r).join(f, f.id == r.transaction_import_file_id).where(r.transaction_fact_id == fact_id)

    def sources(self, fact_id):
        return [dict(row) for row in self.db.execute(self.source_statement(fact_id).order_by(TransactionImportRow.id).limit(4001)).mappings()]

    def source_page(self, fact_id, request):
        return page_rows(self.db, self.source_statement(fact_id), request,
            dict(id=TransactionImportRow.id,source_file_id=TransactionImportFile.id))

    def originals(self, fact_id):
        a, r, l = ReviewAllocation, ReviewCase, LedgerEntry
        scope = select(a.review_id).where(a.transaction_id == fact_id)
        flows = select(a.ledger_id).where(a.transaction_id == fact_id)
        def rows(statement):
            return [dict(row) for row in self.db.execute(statement.limit(4001)).mappings()]
        return (rows(select(*a.__table__.columns).where(a.transaction_id == fact_id).order_by(a.id)),
            rows(select(*r.__table__.columns).where(r.id.in_(scope)).order_by(r.id)),
            rows(select(*l.__table__.columns).where(l.id.in_(flows)).order_by(l.id)))

    def allocation_page(self, fact_id, request):
        a, r, l = ReviewAllocation, ReviewCase, LedgerEntry
        statement = select(*a.__table__.columns,
            *[column.label("review__" + column.name) for column in r.__table__.columns],
            *[column.label("ledger__" + column.name) for column in l.__table__.columns]).select_from(a).join(r, r.id == a.review_id).join(l,l.id == a.ledger_id)
        return page_rows(self.db, statement, request, dict(id=a.id,review_id=a.review_id,ledger_id=a.ledger_id), condition=a.transaction_id == fact_id)
