from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.schema.list_query import ListSorter


class TransactionFactFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cash_direction: Literal["IN", "OUT"] | None = None
    currency_code: str | None = None
    account_code: str | None = None
    date_from: date | None = None
    date_to: date | None = None


class TransactionFactSorter(ListSorter):
    field: Literal[
        "id",
        "occurred_time",
        "amount",
        "created_time",
        "updated_time",
    ] = "occurred_time"


class TransactionFactListItem(BaseModel):
    id: int
    occurred_time: datetime
    cash_direction: Literal["IN", "OUT"]
    amount: int
    currency_code: str
    account_code: str
    counterparty: str
    summary: str
    created_time: datetime
    updated_time: datetime


class TransactionFactRead(TransactionFactListItem):
    fact_key: str


class TransactionFactPageRead(BaseModel):
    items: list[TransactionFactListItem]
    total: int
    page: int
    page_size: int
    q: str
    filter: TransactionFactFilter
    sorter: TransactionFactSorter


class TransactionFactPageResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: TransactionFactPageRead


class TransactionFactImportEvidenceRead(BaseModel):
    raw_id: int
    import_file_id: int
    source_row_number: int
    source_reference: str
    parse_status: str
    issue_code: str
    filename: str
    source_type: str
    institution_code: str
    file_format: str
    imported_time: datetime


class TransactionFactAllocationRead(BaseModel):
    id: int
    review_id: int
    fact_id: int
    economic_id: int
    amount: int
    currency_code: str


class TransactionFactReviewRead(BaseModel):
    id: int
    review_type: str
    behavior_code: str
    status: str
    version: int
    title: str
    created_time: datetime
    updated_time: datetime


class TransactionFactLedgerRead(BaseModel):
    id: int
    economic_type: Literal["TRANSACTION", "ACCOUNT_TRANSFER", "CLAIM"]
    cash_direction: Literal["IN", "OUT"]
    amount: int
    currency_code: str
    account_code: str
    occurred_time: datetime


class TransactionFactDetailRead(BaseModel):
    transaction_fact: TransactionFactRead
    import_evidence: list[TransactionFactImportEvidenceRead]
    allocations: list[TransactionFactAllocationRead]
    reviews: list[TransactionFactReviewRead]
    ledgers: list[TransactionFactLedgerRead]


class TransactionFactDetailResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: TransactionFactDetailRead
