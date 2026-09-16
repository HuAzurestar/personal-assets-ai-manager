from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


@dataclass(frozen=True, slots=True)
class EconomicPageQuery:
    page: int = 1
    page_size: int = 50
    date_from: date | None = None
    date_to: date | None = None
    economic_type: tuple[str, ...] = ()
    currency_code: tuple[str, ...] = ()
    q: str = ""


@dataclass(frozen=True, slots=True)
class EconomicSummaryQuery:
    date_from: date | None = None
    date_to: date | None = None


class EconomicMoneyRead(BaseModel):
    amount_value: int
    amount_scale: int
    currency_code: str


class EconomicTagRead(BaseModel):
    view_name: str
    view_system_name: str
    tag_name: str
    tag_system_name: str


class EconomicFlowListItem(BaseModel):
    id: int
    economic_type: Literal["TRANSACTION", "ACCOUNT_TRANSFER", "CLAIM"]
    cash_direction: Literal["IN", "OUT"]
    amount: EconomicMoneyRead
    title: str
    start_time: datetime
    end_time: datetime
    claim_key: str
    claim_side: str
    reversal_of_id: int
    projection_version: int
    tags: list[EconomicTagRead] = Field(default_factory=list)


class EconomicFlowPageRead(BaseModel):
    items: list[EconomicFlowListItem]
    total: int
    page: int
    page_size: int
    filters: dict[str, object]


class EconomicFlowPageResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: EconomicFlowPageRead


class EconomicAllocationEvidenceRead(BaseModel):
    id: int
    review_id: int
    fact_id: int
    economic_id: int
    role: str
    amount: EconomicMoneyRead


class EconomicFactBriefRead(BaseModel):
    id: int
    occurred_time: datetime
    cash_direction: str
    amount: EconomicMoneyRead
    account_code: str
    counterparty: str
    summary: str


class EconomicReviewBriefRead(BaseModel):
    id: int
    behavior_code: str
    status: str
    version: int
    title: str


class EconomicFlowDetailRead(BaseModel):
    flow: EconomicFlowListItem
    allocations: list[EconomicAllocationEvidenceRead]
    facts: list[EconomicFactBriefRead]
    reviews: list[EconomicReviewBriefRead]


class EconomicFlowDetailResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: EconomicFlowDetailRead


class EconomicCurrencySummaryRead(BaseModel):
    currency_code: str
    amount_scale: int
    income_value: int
    expense_value: int
    reversal_in_value: int
    reversal_out_value: int
    account_transfer_in_value: int
    account_transfer_out_value: int
    account_transfer_net_value: int
    claim_in_value: int
    claim_out_value: int
    receivable_balance_value: int
    payable_balance_value: int


class EconomicSummaryRead(BaseModel):
    entry_count: int
    totals: list[EconomicCurrencySummaryRead]
    basis_version: str = "economic-flow-v2"


class EconomicSummaryResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: EconomicSummaryRead
