"""Immutable normalized Fact projections and scoped, paged source evidence."""
from backend.core.import_public_text import masked_reference, masked_summary
from backend.core.money import normalize_currency_code
from backend.entity import ReviewAllocation, ReviewCase, LedgerEntry
from backend.error import TargetEconomicError, TargetFactError
from backend.mapper.bounded_query_mapper import query_budget
from backend.mapper.fact_read_mapper import FactReadMapper
from backend.mapper.trusted_relation_mapper import TrustedRelationMapper
from backend.service.flow_read_service import limited
from backend.service.review_read_service import ReviewReadService
from backend.service.review_command_service import flow_po
from backend.schema.fact_query import TransactionFactDetailRead


def fact_po(row, *, detail=False):
    try:
        valid = type(row["amount"]) is int and 0 < row["amount"] <= 9_000_000_000_000 and normalize_currency_code(row["currency_code"]) == row["currency_code"]
    except ValueError:
        valid = False
    if not valid or row["cash_direction"] not in (1,2,"IN","OUT"):
        raise TargetEconomicError(409,"Fact financial fields are damaged",code="RELATION_BROKEN")
    fields = ("id","occurred_time","amount","currency_code","created_time","updated_time")
    result = {key:row[key] for key in fields} | dict(cash_direction="IN" if row["cash_direction"] in (1,"IN") else "OUT",
        account_code=masked_reference(row["account_code"]),counterparty_account_ref=masked_reference(row["counterparty_account_ref"]),
        counterparty_name=masked_summary(row["counterparty_name"]),summary=masked_summary(row["summary"]))
    if detail:
        result["fact_key"] = row["fact_key"]
    return result


class FactReadService:
    def __init__(self, db):
        self.mapper = FactReadMapper(db)
        self.relations = TrustedRelationMapper(db)

    def _snapshot(self):
        self.relations.read_snapshot()
        self.relations.validate()

    def _get(self, fact_id):
        if type(fact_id) is not int or not 1 <= fact_id <= 2**63 - 1:
            raise TargetEconomicError(422,"invalid Fact identity",code="LIST_FILTER_VALUE_INVALID")
        row = self.mapper.get(fact_id)
        if row is None:
            raise TargetFactError(404,"transaction Fact not found")
        return row

    @staticmethod
    def source_po(row):
        return row | dict(source_reference=masked_reference(row["source_reference"]),filename=masked_summary(row["filename"]))

    def page(self, request):
        with query_budget(self.mapper.db):
            self._snapshot()
            rows,total = self.mapper.page(request)
            return limited(dict(items=[fact_po(row) for row in rows],total=total,page_index=request.page_index,page_size=request.page_size))

    def search(self, request):
        with query_budget(self.mapper.db):
            self._snapshot()
            result = self.mapper.search(request, lambda row:row | fact_po(row))
            result["items"] = [fact_po(row) for row in result["items"]]
            return limited(result)

    def detail(self, fact_id):
        with query_budget(self.mapper.db):
            self._snapshot()
            fact = self._get(fact_id)
            self.mapper.source_guard(fact_id)
            sources = self.mapper.sources(fact_id)
            allocations,reviews,ledgers = self.mapper.originals(fact_id)
            if 1 + sum(map(len,(sources,allocations,reviews,ledgers))) > 4000:
                raise TargetEconomicError(413,"use complete paged Fact relations",code="DETAIL_LIMIT")
            active = {row["id"] for row in reviews if row["status"] == 0}
            if sum(row["cash_amount"] for row in allocations if row["review_id"] in active) > fact["amount"]:
                raise TargetEconomicError(409,"Fact is overallocated",code="RELATION_BROKEN")
            return limited(TransactionFactDetailRead(transaction_fact=fact_po(fact,detail=True),
                import_evidence=[self.source_po(row) for row in sources],allocations=allocations,
                reviews=[ReviewReadService.po(row) for row in reviews],ledgers=[flow_po(row) for row in ledgers]))

    def allocation_page(self, fact_id, request):
        with query_budget(self.mapper.db):
            self._snapshot()
            self._get(fact_id)
            rows,total = self.mapper.allocation_page(fact_id,request)
            items = []
            for row in rows:
                review = {column.name:row["review__" + column.name] for column in ReviewCase.__table__.columns}
                ledger = {column.name:row["ledger__" + column.name] for column in LedgerEntry.__table__.columns}
                allocation = {column.name:row[column.name] for column in ReviewAllocation.__table__.columns}
                items.append(dict(allocation=allocation,ledger_entry=flow_po(ledger),review=ReviewReadService.po(review)))
            return limited(dict(items=items,total=total,page_index=request.page_index,page_size=request.page_size))

    def source_page(self, fact_id, request):
        with query_budget(self.mapper.db):
            self._snapshot()
            self._get(fact_id)
            self.mapper.source_guard(fact_id)
            rows,total = self.mapper.source_page(fact_id,request)
            return limited(dict(items=[self.source_po(row) for row in rows],total=total,page_index=request.page_index,page_size=request.page_size))
