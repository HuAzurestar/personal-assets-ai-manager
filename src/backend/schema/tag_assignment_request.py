from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.error import ListQueryError
from backend.schema.list_query import (
    ListRequest,
    ListSorter,
    iter_filter_fields,
    validate_list_capabilities,
)
from backend.schema.response import ListBody, ListResponse, SuccessResponse


class TagAssignmentRequestRead(BaseModel):
    id: int
    rule_id: int
    rule_revision: int
    rule_name: str
    ledger_id: int
    ledger_active: bool
    ledger_summary: str | None = None
    ledger_counterparty_name: str | None = None
    ledger_amount: int | None = None
    ledger_currency_code: str | None = None
    view_id: int
    view_name: str
    view_system_name: str
    proposed_tag_id: int
    proposed_tag_name: str
    proposed_tag_system_name: str
    status: Literal[1, 2, 3, 4, 5]
    reason_summary: str
    created_time: datetime
    updated_time: datetime


class TagEligibility(BaseModel):
    can_approve: bool
    code: str


class TagAssignmentRequestDetail(TagAssignmentRequestRead):
    eligibility: TagEligibility


class TagAssignmentRequestResponse(SuccessResponse[TagAssignmentRequestDetail]):
    body: TagAssignmentRequestDetail


class TagAssignmentRequestFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: int | None = None
    view_id: int | None = None
    ledger_id: int | None = None
    status: Literal[1, 2, 3, 4, 5] | None = None


class TagAssignmentRequestSorter(ListSorter):
    field: Literal["created_time"] = "created_time"


class TagAssignmentRequestListRequest(ListRequest):
    @model_validator(mode="after")
    def validate_capabilities(self) -> TagAssignmentRequestListRequest:
        validate_list_capabilities(
            self,
            query_fields=(),
            filter_operators={
                "rule_id": ("=",),
                "view_id": ("=",),
                "ledger_id": ("=",),
                "status": ("=",),
            },
            sorter_fields=("created_time",),
            logical_operators=("AND",),
            max_sorters=1,
        )
        _validate_filter_values(self)
        return self


class TagAssignmentRequestListBody(ListBody[TagAssignmentRequestRead]):
    pass


class TagAssignmentRequestListResponse(ListResponse[TagAssignmentRequestRead]):
    body: TagAssignmentRequestListBody


class TagAssignmentBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    request_ids: list[int] = Field(min_length=1, max_length=100)

    @field_validator("request_ids")
    @classmethod
    def validate_request_ids(cls, value: list[int]) -> list[int]:
        if any(item < 1 or item > 2**63 - 1 for item in value):
            raise ValueError("request_ids must contain supported positive integer IDs")
        if len(value) != len(set(value)):
            raise ValueError("request_ids must be unique")
        return value


class TagAssignmentItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: int
    result: Literal[
        "APPROVED", "REJECTED", "ALREADY_APPROVED", "ALREADY_REJECTED",
        "SCOPE_CONFLICT", "RULE_STALE", "LEDGER_INACTIVE", "MANUAL_TAG_CONFLICT",
        "NOT_FOUND", "REQUEST_STATE_CONFLICT", "VIEW_INACTIVE", "TAG_INACTIVE",
        "COUNTER_EXHAUSTED",
        "LEDGER_DUPLICATE", "SUGGESTION_STALE",
    ]
    status: Literal[1, 2, 3, 4, 5] | None = None


class TagAssignmentBatchRead(BaseModel):
    operation: Literal["APPROVE", "REJECT"]
    items: list[TagAssignmentItemResult]


class TagAssignmentBatchResponse(SuccessResponse[TagAssignmentBatchRead]):
    body: TagAssignmentBatchRead


def tag_assignment_request_filter(
    request: TagAssignmentRequestListRequest,
) -> TagAssignmentRequestFilter:
    return TagAssignmentRequestFilter.model_validate({
        expression.key: expression.val
        for expression in iter_filter_fields(request.filter)
    })


def _validate_filter_values(request: TagAssignmentRequestListRequest) -> None:
    fields = list(iter_filter_fields(request.filter))
    counts: dict[str, int] = {}
    invalid: list[str] = []
    for expression in fields:
        counts[expression.key] = counts.get(expression.key, 0) + 1
        value = expression.val
        valid_integer = isinstance(value, int) and not isinstance(value, bool)
        if not valid_integer or value < 1 or expression.key == "status" and value not in {1, 2, 3, 4, 5}:
            invalid.append(expression.key)
    duplicates = sorted(key for key, count in counts.items() if count > 1)
    if invalid:
        raise ListQueryError(
            "Invalid tag assignment request filter",
            code="LIST_FILTER_VALUE_INVALID",
            details={
                "component": "filter",
                "invalid_fields": sorted(set(invalid)),
            },
        )
    if duplicates:
        raise ListQueryError(
            "Duplicate tag assignment request filter fields",
            code="LIST_COMBINATION_NOT_SUPPORTED",
            details={"component": "filter", "duplicate_fields": duplicates},
        )
