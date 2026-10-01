"""Canonical existing-v1 Flow reads; shared immutable POs, no second writer."""
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator
from backend.core.money import normalize_currency_code
from backend.error import ListQueryError
from backend.schema.bounded_search import SearchRequest
from backend.schema.list_query import ListRequest, iter_filter_fields, validate_list_capabilities
from backend.schema.response import ListBody, ListResponse, SuccessResponse
from backend.schema.review_read import FlowPO, FirstAllocationPO, ReviewPO, PositionPO, PositionLegPO, PositionAllocationPO
from backend.schema.account_management import AccountRefPO, AccountPO, PartyPO

ECONOMIC_TYPES = ("TRANSACTION", "ACCOUNT_TRANSFER", "ASSET_LIABILITY", "DUPLICATE")


def validate_flow_request(request, *, search=False, relation=False):
    fields = {key: ("=", "!=") for key in ("id", "economic_type", "cash_direction", "cash_currency_code",
        "account_ref_id", "account_id", "party_id", "tag_id", "active")}
    fields["occurred_time"] = (">", ">=", "<", "<=", "between")
    sorters = ("id", "occurred_time", "cash_amount", "signed_cash_amount", "cash_currency_code")
    if relation == "source":
        fields, sorters = {"id": ("=", "!=")}, ("id",)
    elif relation == "tag":
        fields, sorters = {key: ("=", "!=") for key in ("id", "view_id", "tag_id")}, ("id",)
    elif relation:
        fields, sorters = {key: ("=", "!=") for key in ("id", "position_id", "position_leg_id")}, ("id",)
    validate_list_capabilities(request, query_fields=("summary", "counterparty") if search else (),
        filter_operators=fields, sorter_fields=sorters, logical_operators=("AND", "OR", "NOT"), max_sorters=3)
    if len({item.key for item in request.sorter}) != len(request.sorter):
        raise ListQueryError("duplicate sort key", code="LIST_SORTER_INVALID")
    for expression in iter_filter_fields(request.filter):
        key, value, valid = expression.key, expression.val, True
        if key in ("id", "account_ref_id", "account_id", "party_id", "tag_id", "view_id", "position_id", "position_leg_id"):
            valid = type(value) is int and (0 if key in ("account_ref_id", "account_id") else 1) <= value <= 2**63 - 1
        elif key == "active":
            valid = type(value) is bool
        elif key == "economic_type":
            valid = isinstance(value, str) and value in ECONOMIC_TYPES
        elif key == "cash_direction":
            valid = isinstance(value, str) and value in ("IN", "OUT")
        elif key == "cash_currency_code":
            try:
                valid = isinstance(value, str) and normalize_currency_code(value) == value
            except ValueError:
                valid = False
        else:
            def timestamp(item):
                if not isinstance(item, (str, datetime)):
                    raise ValueError("timestamp must be aware")
                parsed = datetime.fromisoformat(item.replace("Z", "+00:00")) if isinstance(item, str) else item
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise ValueError("timestamp must be aware")
                return parsed.astimezone(timezone.utc)
            try:
                if expression.op == "between":
                    if not isinstance(value, dict) or set(value) != {"start", "end"}:
                        raise ValueError("between requires endpoints")
                    expression.val = {key: timestamp(item) for key, item in value.items()}
                    valid = expression.val["start"] < expression.val["end"]
                else:
                    expression.val = timestamp(value)
            except (ValueError, TypeError):
                valid = False
        if not valid:
            raise ListQueryError("invalid Flow filter value", code="LIST_FILTER_VALUE_INVALID", details=dict(field=key))


class FlowListRequest(ListRequest):
    @model_validator(mode="after")
    def capabilities(self):
        validate_flow_request(self)
        return self


class FlowSearchRequest(SearchRequest):
    @model_validator(mode="after")
    def capabilities(self):
        validate_flow_request(self, search=True)
        return self


class FlowRelationRequest(ListRequest):
    @model_validator(mode="after")
    def capabilities(self):
        validate_flow_request(self, relation=True)
        return self


class FlowTagListRequest(ListRequest):
    @model_validator(mode="after")
    def capabilities(self):
        validate_flow_request(self, relation="tag")
        return self


class FlowSourceListRequest(ListRequest):
    @model_validator(mode="after")
    def capabilities(self):
        validate_flow_request(self, relation="source")
        return self


class LedgerEntryListBody(ListBody[FlowPO]):
    pass


class LedgerEntryListResponse(ListResponse[FlowPO]):
    body: LedgerEntryListBody


class FlowSearchBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[FlowPO]
    total: None
    page_size: int
    next_cursor: str | None
    has_more: bool
    scanned_count: int
    elapsed_ms: float


class FlowSearchResponse(SuccessResponse[FlowSearchBatch]):
    pass


class FlowTagRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    view_id: int
    tag_id: int
    view_name: str
    view_system_name: str
    tag_name: str
    tag_system_name: str
    view_status: Literal["ACTIVE", "ARCHIVED"]
    tag_status: Literal["ACTIVE", "ARCHIVED"]
    source_type: Literal["UNKNOWN", "AUTO_RULE"]
    request_id: int | None = None
    rule_id: int | None = None
    rule_revision: int | None = None


class FlowFactRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    occurred_time: datetime
    cash_direction: Literal["IN", "OUT"]
    cash_amount: int
    cash_currency_code: str
    account_code: str
    counterparty_name: str
    summary: str


class FlowAccountOwnership(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["UNIDENTIFIED", "UNASSIGNED", "ASSIGNED"]
    ref: AccountRefPO | None
    account: AccountPO | None
    party: PartyPO | None


class FlowPositionRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allocation: PositionAllocationPO
    position_leg: PositionLegPO
    position: PositionPO
    review: ReviewPO


class FlowSourceRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allocation: FirstAllocationPO
    fact: FlowFactRead
    review: ReviewPO
    ledger_entry: FlowPO
    active: bool
    account: FlowAccountOwnership


class FlowSourceRelationResponse(ListResponse[FlowSourceRelation]):
    pass


class FlowPositionRelationResponse(ListResponse[FlowPositionRelation]):
    pass


class FlowTagListResponse(ListResponse[FlowTagRead]):
    pass


class LedgerEntryDetailRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ledger_entry: FlowPO
    active: bool
    summary: str
    tags: list[FlowTagRead]
    allocations: list[FirstAllocationPO]
    facts: list[FlowFactRead]
    reviews: list[ReviewPO]
    account: FlowAccountOwnership
    positions: list[PositionPO]
    position_legs: list[PositionLegPO]
    position_allocations: list[PositionAllocationPO]
    position_identity_state: Literal["KNOWN", "NEEDS_IDENTITY", "NOT_APPLICABLE"]


class LedgerEntryDetailResponse(SuccessResponse[LedgerEntryDetailRead]):
    pass
