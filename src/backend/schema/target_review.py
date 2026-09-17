from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from backend.error import ListQueryError
from backend.schema.list_query import (
    BetweenValue,
    ListRequest,
    ListSorter,
    iter_filter_fields,
    validate_list_capabilities,
)
from backend.schema.response import ListBody, ListResponse


@dataclass(frozen=True, slots=True)
class TargetReviewFactVO:
    id: int
    occurred_time: datetime
    cash_direction: int
    amount: int
    currency_code: str
    account_code: str
    counterparty_name: str
    counterparty_account_ref: str
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
    amount: int
    currency_code: str


class TargetReviewHistoryRead(BaseModel):
    id: int
    operation: int
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
    field: Literal["id", "created_time", "updated_time", "version"] = "updated_time"


class TargetFactConflictListBody(ListBody[TargetReviewCaseRead]):
    pass


# Production v1 review contract. Scenario names live on Review; Economic has
# only the three agreed cash-flow classifications. Direction, currency,
# account, and occurred_time are derived from the single referenced Fact.
class TargetEconomicDefinitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_key: str = Field(min_length=1, max_length=80)
    economic_type: Literal["TRANSACTION", "ACCOUNT_TRANSFER", "CLAIM"]


class TargetFlowAllocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: int = Field(ge=1)
    economic_key: str = Field(min_length=1, max_length=80)
    amount: int = Field(ge=1)


class TargetEconomicReviewCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    behavior_type: int = Field(ge=0, le=1)
    title: str = Field(default="", max_length=160)
    economics: list[TargetEconomicDefinitionRequest] = Field(min_length=1, max_length=200)
    allocations: list[TargetFlowAllocationRequest] = Field(min_length=1, max_length=500)
    actor: str = Field(default="local-user", min_length=1, max_length=120)
    reason: str = Field(default="", max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=120)


class TargetEconomicFlowRead(BaseModel):
    id: int
    entry_type: int
    entry_direction: int
    amount: int
    currency_code: str
    account_code: str
    counterparty_account_ref: str
    occurred_time: datetime


class TargetEconomicReviewFactRead(BaseModel):
    id: int
    occurred_time: datetime
    cash_direction: int
    amount: int
    currency_code: str
    account_code: str
    counterparty_name: str
    counterparty_account_ref: str
    summary: str


class TargetFlowAllocationRead(BaseModel):
    id: int
    review_case_id: int
    transaction_fact_id: int
    ledger_entry_id: int
    amount: int
    currency_code: str


class TargetEconomicReviewRead(BaseModel):
    id: int
    behavior_type: int
    status: int
    title: str
    facts: list[TargetEconomicReviewFactRead]
    ledger_entries: list[TargetEconomicFlowRead]
    allocations: list[TargetFlowAllocationRead]
    history: list[TargetReviewHistoryRead]
    created_time: datetime
    updated_time: datetime


class TargetEconomicReviewResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: TargetEconomicReviewRead


class TargetFactAllocationCandidateRead(BaseModel):
    id: int
    occurred_time: datetime
    cash_direction: int
    amount: int
    currency_code: str
    account_code: str
    counterparty_name: str
    summary: str
    available_amount: int


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

    cash_direction: int | None = None
    currency_code: str | None = None
    account_code: str | None = None


class TargetReviewCandidateSorter(ListSorter):
    field: Literal["occurred_time"] = "occurred_time"


class TargetReviewCandidateListBody(ListBody[TargetFactAllocationCandidateRead]):
    pass


class TargetReviewCandidateListResponse(ListResponse[TargetFactAllocationCandidateRead]):
    body: TargetReviewCandidateListBody


# Review candidates are a Review creation aid, not a Transaction Fact PO page.


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


def _validate_fact_conflict_filter_values(request: TargetFactConflictListRequest) -> None:
    fields = list(iter_filter_fields(request.filter))
    counts: dict[str, int] = {}
    time_operators: dict[str, list[str]] = {"created_time": [], "updated_time": []}
    for expression in fields:
        counts[expression.key] = counts.get(expression.key, 0) + 1
        value = expression.val
        if expression.key == "id":
            valid = isinstance(value, int) and not isinstance(value, bool) and value >= 1
        elif expression.key == "status":
            valid = isinstance(value, str) and value in {"PENDING", "REJECTED", "CONFIRMED"}
        else:
            time_operators[expression.key].append(expression.op)
            if expression.op == "between":
                between = BetweenValue.model_validate(value)
                valid = parse_review_time(between.start, expression.key) < parse_review_time(between.end, expression.key)
            else:
                parse_review_time(value, expression.key)
                valid = True
        if not valid:
            raise ListQueryError(
                "Invalid Fact Conflict filter value",
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


def _validate_review_candidate_filter_values(request: TargetReviewCandidateListRequest) -> None:
    fields = list(iter_filter_fields(request.filter))
    counts: dict[str, int] = {}
    for expression in fields:
        counts[expression.key] = counts.get(expression.key, 0) + 1
        value = expression.val
        if expression.key == "cash_direction":
            valid = isinstance(value, int) and not isinstance(value, bool) and value in {1, 2}
        elif expression.key == "currency_code":
            valid = isinstance(value, str) and 1 <= len(value.strip()) <= 12
        else:
            valid = isinstance(value, str) and 1 <= len(value.strip()) <= 120
        if not valid:
            raise ListQueryError(
                "Invalid Review candidate filter value",
                code="LIST_FILTER_VALUE_INVALID",
                details={"component": "filter", "key": expression.key, "value": value},
            )
    duplicate_fields = sorted(key for key, count in counts.items() if count > 1)
    if duplicate_fields:
        raise ListQueryError(
            "Filter combination is not supported",
            code="LIST_COMBINATION_NOT_SUPPORTED",
            details={"component": "filter", "duplicate_fields": duplicate_fields},
        )

