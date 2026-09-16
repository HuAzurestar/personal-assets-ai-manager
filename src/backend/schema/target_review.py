from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


# Production Review contract. Scenario names live on Review; LedgerEntry has only the
# three agreed cash-flow classifications. Direction, currency, account and
# occurred_time are derived from the single referenced fact.
class TargetEconomicDefinitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_key: str = Field(min_length=1, max_length=80)
    entry_type: Literal[0, 1, 2]


class TargetFlowAllocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_fact_id: int = Field(ge=1)
    entry_key: str = Field(min_length=1, max_length=80)
    amount_value: int = Field(ge=1)


class TargetEconomicReviewCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    behavior_type: Literal[0, 1] = 0
    title: str = Field(default="", max_length=160)
    entries: list[TargetEconomicDefinitionRequest] = Field(min_length=1, max_length=200)
    allocations: list[TargetFlowAllocationRequest] = Field(min_length=1, max_length=500)
    actor: str = Field(default="local-user", min_length=1, max_length=120)
    reason: str = Field(default="", max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=120)


class TargetEconomicReviewTransitionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor: str = Field(default="local-user", min_length=1, max_length=120)
    reason: str = Field(default="", max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=120)


class TargetReviewRevisionRead(BaseModel):
    id: int
    operation: Literal[0, 1, 2, 3]
    request: dict[str, Any]
    before: dict[str, Any]
    after: dict[str, Any]
    actor: str
    reason: str
    idempotency_key: str
    created_time: datetime


class TargetEconomicFlowRead(BaseModel):
    id: int
    entry_type: Literal[0, 1, 2]
    entry_direction: Literal[1, 2]
    amount_value: int
    amount_scale: int
    currency_code: str
    account_code: str
    counterparty_account_ref: str
    occurred_time: datetime


class TargetFlowAllocationRead(BaseModel):
    id: int
    transaction_fact_id: int
    ledger_entry_id: int
    amount_value: int
    amount_scale: int
    currency_code: str


class TargetEconomicReviewRead(BaseModel):
    id: int
    behavior_type: Literal[0, 1]
    status: Literal[0, 1]
    title: str
    ledger_entries: list[TargetEconomicFlowRead]
    allocations: list[TargetFlowAllocationRead]
    history: list[TargetReviewRevisionRead]
    created_time: datetime
    updated_time: datetime


class TargetEconomicReviewResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: TargetEconomicReviewRead


class TargetEconomicReviewListItem(BaseModel):
    id: int
    behavior_type: Literal[0, 1]
    status: Literal[0, 1]
    title: str
    ledger_entry_count: int
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
    status: Literal[200] = 200
    message: str = "ok"
    body: TargetFactAllocationCandidatePageRead


class TargetFactAllocationCandidateResponse(BaseModel):
    status: Literal["success"] = "success"
    message: str = "ok"
    body: list[TargetFactAllocationCandidateRead]
