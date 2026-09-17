from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import (
    AliasChoices,
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


@dataclass(frozen=True, slots=True)
class TargetReviewFactVO:
    id: int
    occurred_time: datetime
    cash_direction: str
    amount_value: int
    amount_scale: int
    currency_code: str
    account_code: str
    counterparty: str
    summary: str
    fact_key: str
    created_time: datetime
    updated_time: datetime


@dataclass(frozen=True, slots=True)
class TargetReviewIdempotencyVO:
    case_id: int
    operation: str
    request_json: str


class TargetReviewTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    actor: str = Field(default="local-user", min_length=1, max_length=120)
    reason: str = Field(default="", max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=120)


class TargetFactConflictResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution_type: Literal["LINK_EXISTING", "CREATE_NEW"]
    existing_bill_id: int = Field(default=0, ge=0)
    expected_version: int = Field(ge=1)
    actor: str = Field(default="local-user", min_length=1, max_length=120)
    reason: str = Field(default="", max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=120)


class TargetReviewLineRead(BaseModel):
    id: int
    bill_id: int
    role: str
    party: str
    amount_value: int
    amount_scale: int
    currency_code: str


class TargetReviewHistoryRead(BaseModel):
    id: int
    version: int
    operation: str
    schema_version: int
    request: dict[str, Any]
    before: dict[str, Any]
    after: dict[str, Any]
    snapshot_hash: str
    reverses_history_id: int
    actor: str
    reason: str
    idempotency_key: str
    created_time: datetime


class TargetReviewCaseRead(BaseModel):
    id: int
    review_type: str
    status: str
    allocation_status: str
    version: int
    title: str
    result: dict[str, Any]
    lines: list[TargetReviewLineRead]
    history: list[TargetReviewHistoryRead]
    created_time: datetime
    updated_time: datetime


class TargetFactConflictListRequest(ListRequest):
    @model_validator(mode="after")
    def validate_capabilities(self) -> "TargetFactConflictListRequest":
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
        _validate_fact_conflict_filter_values(self)
        return self


class TargetFactConflictFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    status: Literal["PENDING", "REJECTED", "CONFIRMED"] | None = None
    created_time_start: datetime | None = None
    created_time_end: datetime | None = None
    updated_time_start: datetime | None = None
    updated_time_end: datetime | None = None


class TargetFactConflictSorter(ListSorter):
    field: Literal["id", "created_time", "updated_time"] = "updated_time"


class TargetFactConflictListBody(ListBody[TargetReviewCaseRead]):
    pass


# Production v1 review contract. Scenario names live on Review; Economic has
# only the three agreed cash-flow classifications. Direction, currency,
# account, and occurred_time are derived from the single referenced Fact.
class TargetEconomicDefinitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_key: str = Field(min_length=1, max_length=80)
    economic_type: Literal["TRANSACTION", "ACCOUNT_TRANSFER", "CLAIM"] = Field(
        validation_alias=AliasChoices("economic_type", "entry_type")
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_entry_type(cls, value: object) -> object:
        if not isinstance(value, dict) or "economic_type" in value:
            return value
        entry_type = value.get("entry_type")
        if entry_type not in {0, 1, 2}:
            return value
        normalized = dict(value)
        normalized.pop("entry_type")
        normalized["economic_type"] = {
            0: "TRANSACTION",
            1: "ACCOUNT_TRANSFER",
            2: "CLAIM",
        }[entry_type]
        return normalized


class TargetFlowAllocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: int = Field(
        ge=1,
        validation_alias=AliasChoices("fact_id", "transaction_fact_id"),
    )
    economic_key: str = Field(
        min_length=1,
        max_length=80,
        validation_alias=AliasChoices("economic_key", "entry_key"),
    )
    amount_value: int = Field(ge=1)


class TargetEconomicReviewCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    behavior_code: str = Field(min_length=1, max_length=40)
    title: str = Field(
        default="",
        max_length=160,
        validation_alias=AliasChoices("title", "description"),
    )
    result: dict[str, Any] = Field(default_factory=dict)
    economics: list[TargetEconomicDefinitionRequest] = Field(
        min_length=1,
        max_length=200,
        validation_alias=AliasChoices("economics", "entries"),
    )
    allocations: list[TargetFlowAllocationRequest] = Field(min_length=1, max_length=500)
    actor: str = Field(default="local-user", min_length=1, max_length=120)
    reason: str = Field(default="", max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=120)


class TargetEconomicReviewUpdateRequest(TargetEconomicReviewCreateRequest):
    expected_version: int = Field(ge=1)


class TargetEconomicFlowRead(BaseModel):
    id: int
    economic_type: Literal["TRANSACTION", "ACCOUNT_TRANSFER", "CLAIM"]
    cash_direction: Literal["IN", "OUT"]
    amount_value: int
    amount_scale: int
    currency_code: str
    account_code: str
    counterparty_account_ref: str
    occurred_time: datetime


class TargetEconomicReviewFactRead(BaseModel):
    id: int
    occurred_time: datetime
    cash_direction: Literal["IN", "OUT"]
    amount_value: int
    amount_scale: int
    currency_code: str
    account_code: str
    counterparty: str
    summary: str


class TargetFlowAllocationRead(BaseModel):
    id: int
    fact_id: int
    economic_id: int
    amount_value: int
    amount_scale: int
    currency_code: str


class TargetEconomicReviewRead(BaseModel):
    id: int
    behavior_code: str
    status: str
    version: int
    title: str
    result: dict[str, Any]
    facts: list[TargetEconomicReviewFactRead]
    economics: list[TargetEconomicFlowRead]
    allocations: list[TargetFlowAllocationRead]
    history: list[TargetReviewHistoryRead]
    created_time: datetime
    updated_time: datetime


class TargetEconomicReviewResponse(SuccessResponse[TargetEconomicReviewRead]):
    body: TargetEconomicReviewRead


class TargetEconomicReviewListItem(BaseModel):
    id: int
    behavior_code: str
    status: str
    version: int
    title: str
    economic_count: int
    allocation_count: int
    created_time: datetime
    updated_time: datetime


class TargetEconomicReviewListRequest(ListRequest):
    @model_validator(mode="after")
    def validate_capabilities(self) -> "TargetEconomicReviewListRequest":
        validate_list_capabilities(
            self,
            query_fields=(),
            filter_operators={
                "id": ("=",),
                "status": ("=",),
                "created_time": (">=", "<", "between"),
                "updated_time": (">=", "<", "between"),
            },
            sorter_fields=("created_time", "updated_time"),
            logical_operators=("AND",),
            max_sorters=1,
        )
        _validate_review_filter_values(self)
        return self


class TargetEconomicReviewFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    status: Literal["PENDING", "CONFIRMED", "REVOKED"] | None = None
    created_time_start: datetime | None = None
    created_time_end: datetime | None = None
    updated_time_start: datetime | None = None
    updated_time_end: datetime | None = None


class TargetEconomicReviewSorter(ListSorter):
    field: Literal["created_time", "updated_time"] = "updated_time"


class TargetEconomicReviewListBody(ListBody[TargetEconomicReviewListItem]):
    pass


class TargetEconomicReviewListResponse(ListResponse[TargetEconomicReviewListItem]):
    body: TargetEconomicReviewListBody


_REVIEW_DATETIME_ADAPTER = TypeAdapter(datetime)


def parse_review_time(value: object, key: str) -> datetime:
    try:
        parsed = _REVIEW_DATETIME_ADAPTER.validate_python(value)
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


def _validate_fact_conflict_filter_values(
    request: TargetFactConflictListRequest,
) -> None:
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
            valid = isinstance(value, str) and value in {
                "PENDING",
                "REJECTED",
                "CONFIRMED",
            }
        else:
            time_operators[expression.key].add(expression.op)
            if expression.op == "between":
                between = BetweenValue.model_validate(value)
                start = parse_review_time(between.start, expression.key)
                end = parse_review_time(between.end, expression.key)
                valid = start < end
            else:
                parse_review_time(value, expression.key)
                valid = True
        if not valid:
            raise ListQueryError(
                "Invalid Fact Conflict filter value",
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


def _validate_review_filter_values(
    request: TargetEconomicReviewListRequest,
) -> None:
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
            valid = isinstance(value, str) and value in {
                "PENDING",
                "CONFIRMED",
                "REVOKED",
            }
        else:
            time_operators[expression.key].add(expression.op)
            if expression.op == "between":
                between = BetweenValue.model_validate(value)
                start = parse_review_time(between.start, expression.key)
                end = parse_review_time(between.end, expression.key)
                valid = start < end
            else:
                parse_review_time(value, expression.key)
                valid = True
        if not valid:
            raise ListQueryError(
                "Invalid Review filter value",
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


class TargetFactAllocationCandidateRead(BaseModel):
    id: int
    occurred_time: datetime
    cash_direction: str
    amount_value: int
    amount_scale: int
    currency_code: str
    account_code: str
    counterparty: str
    summary: str
    available_value: int


class TargetReviewCandidateListRequest(ListRequest):
    @model_validator(mode="after")
    def validate_capabilities(self) -> "TargetReviewCandidateListRequest":
        validate_list_capabilities(
            self,
            query_fields=(),
            filter_operators={
                "cash_direction": ("=",),
                "currency_code": ("=",),
                "account_code": ("=",),
            },
            sorter_fields=("occurred_time",),
            logical_operators=("AND",),
            max_sorters=1,
        )
        _validate_review_candidate_filter_values(self)
        return self


class TargetReviewCandidateFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cash_direction: Literal["IN", "OUT"] | None = None
    currency_code: str | None = None
    account_code: str | None = None


class TargetReviewCandidateSorter(ListSorter):
    field: Literal["occurred_time"] = "occurred_time"


class TargetReviewCandidateListBody(ListBody[TargetFactAllocationCandidateRead]):
    pass


class TargetReviewCandidateListResponse(ListResponse[TargetFactAllocationCandidateRead]):
    body: TargetReviewCandidateListBody


# Review candidates are a Review creation aid, not a Transaction Fact PO page.


def _validate_review_candidate_filter_values(
    request: TargetReviewCandidateListRequest,
) -> None:
    fields = list(iter_filter_fields(request.filter))
    counts: dict[str, int] = {}
    for expression in fields:
        counts[expression.key] = counts.get(expression.key, 0) + 1
        value = expression.val
        if expression.key == "cash_direction":
            valid = isinstance(value, str) and value in {"IN", "OUT"}
        elif expression.key == "currency_code":
            valid = isinstance(value, str) and 1 <= len(value.strip()) <= 12
        else:
            valid = isinstance(value, str) and 1 <= len(value.strip()) <= 120
        if not valid:
            raise ListQueryError(
                "Invalid Review candidate filter value",
                code="LIST_FILTER_VALUE_INVALID",
                details={
                    "component": "filter",
                    "key": expression.key,
                    "value": value,
                },
            )
    duplicate_fields = sorted(key for key, count in counts.items() if count > 1)
    if duplicate_fields:
        raise ListQueryError(
            "Filter combination is not supported",
            code="LIST_COMBINATION_NOT_SUPPORTED",
            details={
                "component": "filter",
                "duplicate_fields": duplicate_fields,
            },
        )

