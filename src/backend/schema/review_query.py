"""Canonical Review read capabilities, shared with immutable command POs."""
from datetime import datetime, timezone

from pydantic import model_validator
from backend.error import ListQueryError
from backend.schema.list_query import ListRequest, iter_filter_fields, validate_list_capabilities
from backend.schema.bounded_search import SearchRequest
from backend.schema.response import ListResponse, SuccessResponse
from backend.schema.review_read import PO, ReviewPO, FlowPO, FirstAllocationPO, PositionPO, PositionLegPO, PositionAllocationPO

REVIEW_TYPES = ("NORMAL_TRANSACTION", "BORROW_AND_REPAY", "CREDIT_CARD", "SHARED_SETTLEMENT", "OTHER_MANUAL")


def validate_review_query(request, *, search=False, relation=False):
    fields = {name: ("=", "!=") for name in ("id", "type", "status")}
    fields.update({name: (">", ">=", "<", "<=", "between") for name in ("created_time", "updated_time")})
    if relation:
        fields = {"id": ("=", "!=")}
    validate_list_capabilities(request, query_fields=("title",) if search else (), filter_operators=fields,
        sorter_fields=("id",) if relation else ("id", "created_time", "updated_time"),
        logical_operators=("AND", "OR", "NOT"), max_sorters=3)
    if len({row.key for row in request.sorter}) != len(request.sorter):
        raise ListQueryError("duplicate sort key", code="LIST_SORTER_INVALID")
    for expression in iter_filter_fields(request.filter):
        key, value = expression.key, expression.val
        if key == "id":
            valid = type(value) is int and 1 <= value <= 2**63 - 1
        elif key == "type":
            valid = isinstance(value, str) and value in REVIEW_TYPES
        elif key == "status":
            valid = isinstance(value, str) and value in ("CONFIRMED", "REVOKED")
        else:
            def timestamp(item):
                if not isinstance(item, (str, datetime)):
                    raise ValueError("timestamp must be aware")
                result = datetime.fromisoformat(item.replace("Z", "+00:00")) if isinstance(item, str) else item
                if result.tzinfo is None or result.utcoffset() is None:
                    raise ValueError("timestamp must be aware")
                return result.astimezone(timezone.utc)
            try:
                if expression.op == "between":
                    if not isinstance(value, dict) or set(value) != {"start", "end"}:
                        raise ValueError("between requires endpoints")
                    expression.val = {key: timestamp(item) for key, item in value.items()}
                    valid = expression.val["start"] < expression.val["end"]
                else:
                    expression.val = timestamp(value)
                    valid = True
            except (ValueError, TypeError):
                valid = False
        if not valid:
            raise ListQueryError("invalid Review filter", code="LIST_FILTER_VALUE_INVALID", details=dict(field=key))


class ReviewListRequest(ListRequest):
    @model_validator(mode="after")
    def capabilities(self):
        validate_review_query(self)
        return self


class ReviewSearchRequest(SearchRequest):
    @model_validator(mode="after")
    def capabilities(self):
        validate_review_query(self, search=True)
        return self


class ReviewRelationRequest(ListRequest):
    @model_validator(mode="after")
    def capabilities(self):
        validate_review_query(self, relation=True)
        return self


class ReviewCaseListResponse(ListResponse[ReviewPO]):
    pass


class ReviewSearchBatch(PO):
    items: list[ReviewPO]
    total: None
    page_size: int
    next_cursor: str | None
    has_more: bool
    scanned_count: int
    elapsed_ms: float


class ReviewSearchResponse(SuccessResponse[ReviewSearchBatch]):
    pass


class ReviewAllocationListResponse(ListResponse[FirstAllocationPO]):
    pass


class ReviewFlowListResponse(ListResponse[FlowPO]):
    pass


class ReviewLegListResponse(ListResponse[PositionLegPO]):
    pass


class ReviewPositionAllocationListResponse(ListResponse[PositionAllocationPO]):
    pass


class ReviewPositionListResponse(ListResponse[PositionPO]):
    pass
