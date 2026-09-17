from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError, model_validator

from backend.error import ListQueryError
from backend.schema.list_query import (
    BetweenValue,
    ListRequest,
    ListSorter,
    iter_filter_fields,
    validate_list_capabilities,
)
from backend.schema.response import ListBody, ListResponse


class ReviewCaseListRequest(ListRequest):
    @model_validator(mode="after")
    def validate_capabilities(self) -> "ReviewCaseListRequest":
        validate_list_capabilities(
            self,
            query_fields=(),
            filter_operators={
                "id": ("=",),
                "behavior_type": ("=",),
                "status": ("=",),
                "created_time": (">=", "<", "between"),
                "updated_time": (">=", "<", "between"),
            },
            sorter_fields=("id", "created_time", "updated_time"),
            logical_operators=("AND",),
            max_sorters=1,
        )
        _validate_review_case_filter_values(self)
        return self


class ReviewCaseListItem(BaseModel):
    id: int
    behavior_type: int
    status: int
    title: str
    created_time: datetime
    updated_time: datetime
    display_summary: str = ""
    fact_count: int = 0
    allocation_count: int = 0


class ReviewCaseFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    behavior_type: int | None = None
    status: int | None = None
    created_time_start: datetime | None = None
    created_time_end: datetime | None = None
    updated_time_start: datetime | None = None
    updated_time_end: datetime | None = None


class ReviewCaseSorter(ListSorter):
    field: Literal["id", "created_time", "updated_time"] = "updated_time"


class ReviewCaseListBody(ListBody[ReviewCaseListItem]):
    pass


class ReviewCaseListResponse(ListResponse[ReviewCaseListItem]):
    body: ReviewCaseListBody


_DATETIME_ADAPTER = TypeAdapter(datetime)


def parse_review_case_time(value: object, key: str) -> datetime:
    try:
        parsed = _DATETIME_ADAPTER.validate_python(value)
    except ValidationError as error:
        raise ListQueryError(
            "Invalid Review time filter",
            code="LIST_FILTER_VALUE_INVALID",
            details={"component": "filter", "key": key},
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ListQueryError(
            "Review time filter requires a timezone",
            code="LIST_FILTER_VALUE_INVALID",
            details={"component": "filter", "key": key},
        )
    return parsed


def _validate_review_case_filter_values(request: ReviewCaseListRequest) -> None:
    fields = list(iter_filter_fields(request.filter))
    counts: dict[str, int] = {}
    time_operators: dict[str, list[str]] = {"created_time": [], "updated_time": []}
    for expression in fields:
        counts[expression.key] = counts.get(expression.key, 0) + 1
        value = expression.val
        if expression.key == "id":
            valid = isinstance(value, int) and not isinstance(value, bool) and value >= 1
        elif expression.key == "behavior_type":
            valid = isinstance(value, int) and not isinstance(value, bool) and value in {0, 1}
        elif expression.key == "status":
            valid = isinstance(value, int) and not isinstance(value, bool) and value in {0, 1}
        else:
            time_operators[expression.key].append(expression.op)
            if expression.op == "between":
                between = BetweenValue.model_validate(value)
                valid = parse_review_case_time(between.start, expression.key) < parse_review_case_time(between.end, expression.key)
            else:
                parse_review_case_time(value, expression.key)
                valid = True
        if not valid:
            raise ListQueryError(
                "Invalid Review filter value",
                code="LIST_FILTER_VALUE_INVALID",
                details={"component": "filter", "key": expression.key, "value": value},
            )
    duplicate_fields = sorted(key for key, count in counts.items() if key not in time_operators and count > 1)
    invalid_time_fields = sorted(
        key for key, operators in time_operators.items()
        if (operators.count("between") > 0 and len(operators) > 1) or len(set(operators)) != len(operators)
    )
    if duplicate_fields or invalid_time_fields:
        raise ListQueryError(
            "Filter combination is not supported",
            code="LIST_COMBINATION_NOT_SUPPORTED",
            details={"component": "filter", "duplicate_fields": duplicate_fields, "invalid_time_fields": invalid_time_fields},
        )
