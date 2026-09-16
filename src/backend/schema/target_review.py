from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


FINANCIAL_REVIEW_TYPES = (
    "CLASSIFICATION",
    "AA",
    "LOAN_BORROW",
    "LOAN_LEND",
    "REFUND",
    "TRANSFER",
    "FX_EXCHANGE",
    "DUPLICATE",
)


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
class TargetReviewLineWriteVO:
    bill_id: int
    role: str
    party: str
    amount_value: int
    amount_scale: int
    currency_code: str


@dataclass(frozen=True, slots=True)
class TargetReviewIdempotencyVO:
    case_id: int
    operation: str
    request_json: str


class TargetReviewLineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bill_id: int = Field(ge=1)
    role: str = Field(min_length=1, max_length=40)
    party: str = Field(default="", max_length=120)
    amount_value: int | None = Field(default=None, ge=1)


class TargetReviewCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_type: Literal[
        "CLASSIFICATION",
        "AA",
        "LOAN_BORROW",
        "LOAN_LEND",
        "REFUND",
        "TRANSFER",
        "FX_EXCHANGE",
        "DUPLICATE",
    ]
    title: str = Field(default="", max_length=160)
    result: dict[str, Any] = Field(default_factory=dict)
    lines: list[TargetReviewLineRequest] = Field(min_length=1, max_length=200)
    actor: str = Field(default="local-user", min_length=1, max_length=120)
    reason: str = Field(default="", max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=120)


class TargetReviewTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    actor: str = Field(default="local-user", min_length=1, max_length=120)
    reason: str = Field(default="", max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=120)


class TargetReviewUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    title: str = Field(default="", max_length=160)
    result: dict[str, Any] = Field(default_factory=dict)
    lines: list[TargetReviewLineRequest] = Field(min_length=1, max_length=200)
    actor: str = Field(default="local-user", min_length=1, max_length=120)
    reason: str = Field(default="", max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=120)


class TargetAccountSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_code: str = Field(min_length=1, max_length=120)
    expected_projection_version: int = Field(ge=1)
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


class TargetReviewCaseResponse(BaseModel):
    status: Literal["success"] = "success"
    message: str = "ok"
    body: TargetReviewCaseRead


class TargetReviewCaseListResponse(BaseModel):
    status: Literal["success"] = "success"
    message: str = "ok"
    body: list[TargetReviewCaseRead]


class TargetReviewCasePageRead(BaseModel):
    items: list[TargetReviewCaseRead]
    total: int
    page: int
    page_size: int
    status: str
    review_type: str


class TargetReviewCasePageResponse(BaseModel):
    status: Literal["success"] = "success"
    message: str = "ok"
    body: TargetReviewCasePageRead


# V2 flow-review contract.  Scenario names live on Review; Economic has only
# the three agreed accounting natures.  Direction, currency and amount are
# derived from the referenced facts and allocation rows by the service.
class TargetEconomicDefinitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_key: str = Field(min_length=1, max_length=80)
    economic_type: Literal["TRANSACTION", "ACCOUNT_TRANSFER", "CLAIM"]
    title: str = Field(default="", max_length=200)
    claim_key: str = Field(default="", max_length=160)
    claim_side: Literal["UNKNOWN", "RECEIVABLE", "PAYABLE"] = "UNKNOWN"
    reversal_of_id: int = Field(default=0, ge=0)


class TargetFlowAllocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: int = Field(ge=1)
    economic_key: str = Field(min_length=1, max_length=80)
    amount_value: int = Field(ge=1)
    role: str = Field(default="ALLOCATED", min_length=1, max_length=40)


class TargetEconomicReviewCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    behavior_code: str = Field(min_length=1, max_length=40)
    title: str = Field(default="", max_length=160)
    result: dict[str, Any] = Field(default_factory=dict)
    economics: list[TargetEconomicDefinitionRequest] = Field(min_length=1, max_length=200)
    allocations: list[TargetFlowAllocationRequest] = Field(min_length=1, max_length=500)
    actor: str = Field(default="local-user", min_length=1, max_length=120)
    reason: str = Field(default="", max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=120)


class TargetEconomicReviewUpdateRequest(TargetEconomicReviewCreateRequest):
    expected_version: int = Field(ge=1)


class TargetEconomicFlowRead(BaseModel):
    id: int
    economic_type: str
    cash_direction: str
    amount_value: int
    amount_scale: int
    currency_code: str
    title: str
    start_time: datetime
    end_time: datetime
    claim_key: str
    claim_side: str
    reversal_of_id: int
    status: str
    projection_version: int


class TargetFlowAllocationRead(BaseModel):
    id: int
    fact_id: int
    economic_id: int
    amount_value: int
    amount_scale: int
    currency_code: str
    role: str


class TargetEconomicReviewRead(BaseModel):
    id: int
    behavior_code: str
    status: str
    version: int
    title: str
    result: dict[str, Any]
    economics: list[TargetEconomicFlowRead]
    allocations: list[TargetFlowAllocationRead]
    history: list[TargetReviewHistoryRead]
    created_time: datetime
    updated_time: datetime


class TargetEconomicReviewResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
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


class TargetEconomicReviewPageRead(BaseModel):
    items: list[TargetEconomicReviewListItem]
    total: int
    page: int
    page_size: int


class TargetEconomicReviewPageResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: TargetEconomicReviewPageRead


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


class TargetFactAllocationCandidatePageRead(BaseModel):
    items: list[TargetFactAllocationCandidateRead]
    total: int
    page: int
    page_size: int


class TargetFactAllocationCandidatePageResponse(BaseModel):
    status: Literal["success"] = "success"
    message: str = "ok"
    body: TargetFactAllocationCandidatePageRead


class TargetFactAllocationCandidateResponse(BaseModel):
    status: Literal["success"] = "success"
    message: str = "ok"
    body: list[TargetFactAllocationCandidateRead]
