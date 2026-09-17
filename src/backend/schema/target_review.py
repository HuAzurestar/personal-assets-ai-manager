from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from backend.schema.list_query import ListSorter


@dataclass(frozen=True, slots=True)
class TargetReviewFactVO:
    id: int
    occurred_time: datetime
    cash_direction: str
    amount: int
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
    amount: int
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


class TargetFactConflictFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["PENDING", "REJECTED", "CONFIRMED"] | None = None


class TargetFactConflictSorter(ListSorter):
    field: Literal["id", "created_time", "updated_time", "version"] = "updated_time"


class TargetFactConflictPageRead(BaseModel):
    items: list[TargetReviewCaseRead]
    total: int
    page: int
    page_size: int
    q: str
    filter: TargetFactConflictFilter
    sorter: TargetFactConflictSorter


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
    amount: int = Field(ge=1)


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
    amount: int
    currency_code: str
    account_code: str
    counterparty_account_ref: str
    occurred_time: datetime


class TargetEconomicReviewFactRead(BaseModel):
    id: int
    occurred_time: datetime
    cash_direction: Literal["IN", "OUT"]
    amount: int
    currency_code: str
    account_code: str
    counterparty: str
    summary: str


class TargetFlowAllocationRead(BaseModel):
    id: int
    fact_id: int
    economic_id: int
    amount: int
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


class TargetEconomicReviewResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: TargetEconomicReviewRead


class TargetFactAllocationCandidateRead(BaseModel):
    id: int
    occurred_time: datetime
    cash_direction: str
    amount: int
    currency_code: str
    account_code: str
    counterparty: str
    summary: str
    available_value: int


class TargetReviewCandidateFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cash_direction: Literal["IN", "OUT"] | None = None
    currency_code: str | None = None
    account_code: str | None = None


class TargetReviewCandidateSorter(ListSorter):
    field: Literal["id", "occurred_time", "amount", "available_value"] = (
        "occurred_time"
    )


class TargetReviewCandidatePageRead(BaseModel):
    items: list[TargetFactAllocationCandidateRead]
    total: int
    page: int
    page_size: int
    q: str
    filter: TargetReviewCandidateFilter
    sorter: TargetReviewCandidateSorter


class TargetReviewCandidatePageResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: TargetReviewCandidatePageRead


# Review candidates are a Review creation aid, not a Transaction Fact PO page.

