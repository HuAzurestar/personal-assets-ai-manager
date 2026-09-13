from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


FINANCIAL_REVIEW_TYPES = (
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


class TargetReviewCasePageResponse(BaseModel):
    status: Literal["success"] = "success"
    message: str = "ok"
    body: TargetReviewCasePageRead
