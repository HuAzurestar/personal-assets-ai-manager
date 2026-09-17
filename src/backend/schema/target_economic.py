from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schema.list_query import ListSorter


class EconomicFlowFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    economic_type: list[Literal["TRANSACTION", "ACCOUNT_TRANSFER", "CLAIM"]] = Field(default_factory=list)
    cash_direction: Literal["IN", "OUT"] | None = None
    currency_code: list[str] = Field(default_factory=list)
    account_code: str | None = None
    date_from: date | None = None
    date_to: date | None = None


class EconomicFlowSorter(ListSorter):
    field: Literal[
        "id",
        "occurred_time",
        "amount",
        "projection_version",
    ] = "occurred_time"


@dataclass(frozen=True, slots=True)
class EconomicPageQuery:
    page: int = 1
    page_size: int = 20
    date_from: date | None = None
    date_to: date | None = None
    entry_type: tuple[int, ...] = ()
    currency_code: tuple[str, ...] = ()
    cash_direction: int | None = None
    account_code: str = ""
    q: str = ""
    sort_field: str = "occurred_time"
    sort_order: str = "desc"


@dataclass(frozen=True, slots=True)
class EconomicSummaryQuery:
    date_from: date | None = None
    date_to: date | None = None


class EconomicMoneyRead(BaseModel):
    amount: int
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
    account_code: str
    counterparty_account_ref: str
    projection_version: int
    occurred_time: datetime


class EconomicFlowPageRead(BaseModel):
    items: list[EconomicFlowListItem]
    total: int
    page: int
    page_size: int
    q: str
    filter: EconomicFlowFilter
    sorter: EconomicFlowSorter


class EconomicFlowPageResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: EconomicFlowPageRead


class EconomicAllocationEvidenceRead(BaseModel):
    id: int
    review_id: int
    fact_id: int
    economic_id: int
    amount: EconomicMoneyRead


class EconomicFactBriefRead(BaseModel):
    id: int
    occurred_time: datetime
    cash_direction: str
    amount: EconomicMoneyRead
    account_code: str
    account_review_version: int
    counterparty: str
    summary: str


class EconomicReviewBriefRead(BaseModel):
    id: int
    review_type: str
    behavior_code: str
    status: str
    version: int
    title: str


class EconomicFlowDetailItem(EconomicFlowListItem):
    tags: list[EconomicTagRead] = Field(default_factory=list)


class EconomicFlowDetailRead(BaseModel):
    flow: EconomicFlowDetailItem
    allocations: list[EconomicAllocationEvidenceRead]
    facts: list[EconomicFactBriefRead]
    reviews: list[EconomicReviewBriefRead]


class EconomicFlowDetailResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: EconomicFlowDetailRead


class EconomicCurrencySummaryRead(BaseModel):
    currency_code: str
    transaction_in_value: int
    transaction_out_value: int
    account_transfer_in_value: int
    account_transfer_out_value: int
    claim_cashflow_in_value: int
    claim_cashflow_out_value: int


class EconomicSummaryRead(BaseModel):
    entry_count: int
    totals: list[EconomicCurrencySummaryRead]
    basis_version: str = "economic-flow-v1"


class EconomicSummaryResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: EconomicSummaryRead
