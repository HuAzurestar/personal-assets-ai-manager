from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from backend.error import ListQueryError
from backend.schema.list_query import (
    BetweenValue,
    ListRequest,
    ListSorter,
    iter_filter_fields,
    validate_list_capabilities,
)
from backend.schema.response import ListBody, ListResponse, SuccessResponse


class TargetTagViewCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    system_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")


class TargetTagCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    system_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")


class TargetTagSystemNamePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)


class TargetTagSystemNameRead(BaseModel):
    system_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")


class TargetTagSystemNameResponse(SuccessResponse[TargetTagSystemNameRead]):
    body: TargetTagSystemNameRead


class TargetTagStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ACTIVE", "ARCHIVED"]


class TargetTagAssignmentRequest(BaseModel):
    """The complete effective Tag state owned directly by one Ledger."""

    model_config = ConfigDict(extra="forbid")

    tag_state: dict[str, str]


class TargetTagRead(BaseModel):
    id: int
    name: str
    system_name: str
    status: str


class TargetTagViewRead(BaseModel):
    id: int
    name: str
    system_name: str
    status: str
    tags: list[TargetTagRead]
    created_time: datetime
    updated_time: datetime


class TargetTagViewListRequest(ListRequest):
    @model_validator(mode="after")
    def validate_capabilities(self) -> "TargetTagViewListRequest":
        validate_list_capabilities(
            self,
            query_fields=(),
            filter_operators={
                "id": ("=",),
                "status": ("=",),
                "created_time": (">=", "<", "between"),
                "updated_time": (">=", "<", "between"),
            },
            sorter_fields=("id", "created_time", "updated_time"),
            logical_operators=("AND",),
            max_sorters=1,
        )
        _validate_tag_view_filter_values(self)
        return self


class TargetTagViewFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    status: Literal["ACTIVE", "ARCHIVED"] | None = None
    created_time_start: datetime | None = None
    created_time_end: datetime | None = None
    updated_time_start: datetime | None = None
    updated_time_end: datetime | None = None


class TargetTagViewSorter(ListSorter):
    field: Literal["id", "created_time", "updated_time"] = "id"


class TargetTagViewListBody(ListBody[TargetTagViewRead]):
    pass


class TargetTagViewResponse(SuccessResponse[TargetTagViewRead]):
    body: TargetTagViewRead


class TargetTagViewListResponse(ListResponse[TargetTagViewRead]):
    body: TargetTagViewListBody


class TargetTagAssignmentRead(BaseModel):
    ledger_id: int
    tag_state: dict[str, str]


class TargetTagAssignmentResponse(SuccessResponse[TargetTagAssignmentRead]):
    body: TargetTagAssignmentRead


_TAG_VIEW_DATETIME_ADAPTER = TypeAdapter(datetime)


def parse_tag_view_time(value: object, key: str) -> datetime:
    try:
        parsed = _TAG_VIEW_DATETIME_ADAPTER.validate_python(value)
    except ValidationError as error:
        raise ListQueryError(
            "Invalid Tag View time filter",
            code="LIST_FILTER_VALUE_INVALID",
            details={"component": "filter", "key": key},
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ListQueryError(
            "Tag View time filter requires a timezone",
            code="LIST_FILTER_VALUE_INVALID",
            details={"component": "filter", "key": key},
        )
    return parsed.astimezone(timezone.utc)


def _validate_tag_view_filter_values(request: TargetTagViewListRequest) -> None:
    fields = list(iter_filter_fields(request.filter))
    counts: dict[str, int] = {}
    time_operators: dict[str, set[str]] = {
        "created_time": set(),
        "updated_time": set(),
    }
    for expression in fields:
        counts[expression.key] = counts.get(expression.key, 0) + 1
        value = expression.val
        if expression.key == "id":
            valid = isinstance(value, int) and not isinstance(value, bool) and value >= 1
        elif expression.key == "status":
            valid = isinstance(value, str) and value in {"ACTIVE", "ARCHIVED"}
        else:
            time_operators[expression.key].add(expression.op)
            if expression.op == "between":
                between = BetweenValue.model_validate(value)
                start = parse_tag_view_time(between.start, expression.key)
                end = parse_tag_view_time(between.end, expression.key)
                valid = start < end
            else:
                parse_tag_view_time(value, expression.key)
                valid = True
        if not valid:
            raise ListQueryError(
                "Invalid Tag View filter value",
                code="LIST_FILTER_VALUE_INVALID",
                details={
                    "component": "filter",
                    "key": expression.key,
                    "value": value,
                },
            )

    duplicate_fields = sorted(
        key for key, count in counts.items()
        if key not in time_operators and count > 1
    )
    invalid_time_fields = sorted(
        key
        for key, operators in time_operators.items()
        if (
            ("between" in operators and len(operators) > 1)
            or len(operators) != counts.get(key, 0)
        )
    )
    if duplicate_fields or invalid_time_fields:
        raise ListQueryError(
            "Filter combination is not supported",
            code="LIST_COMBINATION_NOT_SUPPORTED",
            details={
                "component": "filter",
                "duplicate_fields": duplicate_fields,
                "invalid_time_fields": invalid_time_fields,
            },
        )
