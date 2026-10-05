"""Bounded immutable Fact reads; source text is always a public projection."""
from datetime import datetime
from types import SimpleNamespace
from pydantic import model_validator
from backend.error import ListQueryError
from backend.schema.bounded_search import SearchRequest
from backend.schema.list_query import ListRequest, iter_filter_fields, validate_list_capabilities
from backend.schema.flow_read import validate_flow_request
from backend.schema.review_read import PO, FlowPO, ReviewPO, FirstAllocationPO
from backend.schema.response import ListResponse, SuccessResponse
from backend.schema.transaction_fact import TransactionFactListItem, TransactionFactRead
from backend.core.money import normalize_currency_code


def validate_fact_query(request, *, search=False):
    fields = {key: ("=", "!=") for key in ("id", "cash_direction", "currency_code", "account_code",
        "account_ref_id", "account_id", "party_id")}
    fields["occurred_time"] = (">", ">=", "<", "<=", "between")
    validate_list_capabilities(request, query_fields=("summary", "counterparty_name") if search else (), filter_operators=fields,
        sorter_fields=("id", "occurred_time", "amount", "signed_amount", "currency_code"),
        logical_operators=("AND", "OR", "NOT"), max_sorters=3)
    if len({item.key for item in request.sorter}) != len(request.sorter):
        raise ListQueryError("duplicate sort key", code="LIST_SORTER_INVALID")
    for expression in iter_filter_fields(request.filter):
        key, value = expression.key, expression.val
        if key == "account_code":
            valid = isinstance(value, str) and 1 <= len(value) <= 120
        elif key == "currency_code":
            try:
                valid = isinstance(value, str) and normalize_currency_code(value) == value
            except ValueError:
                valid = False
        elif key == "cash_direction":
            # Explicit read-only transition for old Fact callers. New POs and
            # frontend use semantic direction; no alternate financial writer.
            if type(value) is int and value in (1, 2):
                expression.val = "IN" if value == 1 else "OUT"
            valid = expression.val in ("IN", "OUT")
        else:
            # Reuse already tested ID/aware-time grammar without introducing a
            # second timestamp parser. This has no SQL and preserves expression.
            validate_flow_request(SimpleNamespace(query=[], sorter=[], filter=expression))
            valid = True
        if not valid:
            raise ListQueryError("invalid Fact filter value", code="LIST_FILTER_VALUE_INVALID", details=dict(field=key))


class FactListRequest(ListRequest):
    @model_validator(mode="after")
    def capabilities(self):
        validate_fact_query(self)
        return self


class FactSearchRequest(SearchRequest):
    @model_validator(mode="after")
    def capabilities(self):
        validate_fact_query(self, search=True)
        return self


class FactRelationRequest(ListRequest):
    @model_validator(mode="after")
    def capabilities(self):
        validate_list_capabilities(self, query_fields=(), filter_operators={key: ("=", "!=") for key in
            ("id", "review_id", "ledger_id")}, sorter_fields=("id",), logical_operators=("AND", "OR", "NOT"), max_sorters=1)
        for expression in iter_filter_fields(self.filter):
            if type(expression.val) is not int or not 1 <= expression.val <= 2**63 - 1:
                raise ListQueryError("invalid relation ID", code="LIST_FILTER_VALUE_INVALID")
        return self


class FactSourceRequest(ListRequest):
    @model_validator(mode="after")
    def capabilities(self):
        validate_list_capabilities(self, query_fields=(), filter_operators={key: ("=", "!=") for key in
            ("id", "source_file_id")}, sorter_fields=("id",), logical_operators=("AND", "OR", "NOT"), max_sorters=1)
        for expression in iter_filter_fields(self.filter):
            if type(expression.val) is not int or not 1 <= expression.val <= 2**63 - 1:
                raise ListQueryError("invalid source ID", code="LIST_FILTER_VALUE_INVALID")
        return self


class TransactionFactListResponse(ListResponse[TransactionFactListItem]):
    pass


class FactSearchBatch(PO):
    items: list[TransactionFactListItem]
    total: None
    page_size: int
    next_cursor: str | None
    has_more: bool
    scanned_count: int
    elapsed_ms: float


class FactSearchResponse(SuccessResponse[FactSearchBatch]):
    pass


class FactSourcePO(PO):
    id: int
    source_file_id: int
    transaction_id: int
    source_row_number: int
    source_reference: str
    row_status: int
    issue_code: str
    filename: str
    source_type: int
    file_format: int
    imported_time: datetime


class FactAllocationRead(PO):
    allocation: FirstAllocationPO
    ledger_entry: FlowPO
    review: ReviewPO


class FactAllocationListResponse(ListResponse[FactAllocationRead]):
    pass


class FactSourceListResponse(ListResponse[FactSourcePO]):
    pass


class TransactionFactDetailRead(PO):
    transaction_fact: TransactionFactRead
    import_evidence: list[FactSourcePO]
    allocations: list[FirstAllocationPO]
    ledgers: list[FlowPO]
    reviews: list[ReviewPO]


class TransactionFactDetailResponse(SuccessResponse[TransactionFactDetailRead]):
    pass
