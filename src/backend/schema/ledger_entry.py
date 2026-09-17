from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schema.list_query import ListSorter


class LedgerEntryFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_type: list[int] = Field(default_factory=list)
    entry_direction: int | None = None
    currency_code: list[str] = Field(default_factory=list)
    account_code: str | None = None
    date_from: date | None = None
    date_to: date | None = None


class LedgerEntrySorter(ListSorter):
    field: Literal[
        "id",
        "occurred_time",
        "amount",
        "created_time",
        "updated_time",
    ] = "occurred_time"


@dataclass(frozen=True, slots=True)
class LedgerEntryPageQuery:
    page: int = 1
    page_size: int = 20
    date_from: date | None = None
    date_to: date | None = None
    entry_type: tuple[int, ...] = ()
    currency_code: tuple[str, ...] = ()
    entry_direction: int | None = None
    account_code: str = ""
    q: str = ""
    sort_field: str = "occurred_time"
    sort_order: str = "desc"


@dataclass(frozen=True, slots=True)
class LedgerEntrySummaryQuery:
    date_from: date | None = None
    date_to: date | None = None


class LedgerEntryTagRead(BaseModel):
    view_name: str
    view_system_name: str
    tag_name: str
    tag_system_name: str


class LedgerEntryListItem(BaseModel):
    id: int
    entry_type: int
    entry_direction: int
    amount: int
    currency_code: str
    account_code: str
    counterparty_account_ref: str
    occurred_time: datetime
    created_time: datetime
    updated_time: datetime


class LedgerEntryPageRead(BaseModel):
    items: list[LedgerEntryListItem]
    total: int
    page: int
    page_size: int
    q: str
    filter: LedgerEntryFilter
    sorter: LedgerEntrySorter


class LedgerEntryPageResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: LedgerEntryPageRead


class LedgerAllocationEvidenceRead(BaseModel):
    id: int
    review_case_id: int
    transaction_fact_id: int
    ledger_entry_id: int
    amount: int
    currency_code: str


class LedgerFactBriefRead(BaseModel):
    id: int
    occurred_time: datetime
    cash_direction: int
    amount: int
    currency_code: str
    account_code: str
    counterparty_name: str
    counterparty_account_ref: str
    summary: str


class LedgerReviewBriefRead(BaseModel):
    id: int
    behavior_type: int
    status: int
    title: str
    created_time: datetime
    updated_time: datetime


class LedgerEntryDetailItem(LedgerEntryListItem):
    tags: list[LedgerEntryTagRead] = Field(default_factory=list)


class LedgerEntryDetailRead(BaseModel):
    ledger_entry: LedgerEntryDetailItem
    allocations: list[LedgerAllocationEvidenceRead]
    facts: list[LedgerFactBriefRead]
    reviews: list[LedgerReviewBriefRead]


class LedgerEntryDetailResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: LedgerEntryDetailRead


class LedgerCurrencySummaryRead(BaseModel):
    currency_code: str
    income_and_expense_in_amount: int
    income_and_expense_out_amount: int
    internal_transfer_in_amount: int
    internal_transfer_out_amount: int
    asset_and_liability_in_amount: int
    asset_and_liability_out_amount: int


class LedgerEntrySummaryRead(BaseModel):
    entry_count: int
    totals: list[LedgerCurrencySummaryRead]


class LedgerEntrySummaryResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: LedgerEntrySummaryRead
