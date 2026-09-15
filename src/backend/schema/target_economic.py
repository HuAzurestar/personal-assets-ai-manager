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
    entry_type: tuple[int, ...] = ()
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
    entry_type: Literal[0, 1, 2]
    entry_direction: Literal[1, 2]
    amount: EconomicMoneyRead
    account_code: str
    counterparty_account_ref: str
    occurred_time: datetime
    tags: list[EconomicTagRead] = Field(default_factory=list)


class EconomicFlowPageRead(BaseModel):
    items: list[EconomicFlowListItem]
    total: int
    page: int
    page_size: int
    filters: dict[str, object]


class EconomicAllocationEvidenceRead(BaseModel):
    id: int
    review_case_id: int
    transaction_fact_id: int
    ledger_entry_id: int
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
    behavior_code: str
    status: str
    version: int
    description: str


class EconomicFlowDetailRead(BaseModel):
    entry: EconomicFlowListItem
    tag_review_version: int
    allocations: list[EconomicAllocationEvidenceRead]
    facts: list[EconomicFactBriefRead]
    reviews: list[EconomicReviewBriefRead]


class EconomicCurrencySummaryRead(BaseModel):
    currency_code: str
    amount_scale: int
    transaction_in_value: int
    transaction_out_value: int
    account_transfer_in_value: int
    account_transfer_out_value: int
    claim_cashflow_in_value: int
    claim_cashflow_out_value: int


class EconomicSummaryRead(BaseModel):
    entry_count: int
    totals: list[EconomicCurrencySummaryRead]
    basis_version: str = "ledger-entry-v2"
