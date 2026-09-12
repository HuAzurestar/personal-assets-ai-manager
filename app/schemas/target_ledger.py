from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class TargetLedgerSummaryQuery:
    date_from: date | None = None
    date_to: date | None = None


@dataclass(frozen=True, slots=True)
class TargetLedgerPageQuery:
    page: int = 1
    page_size: int = 50
    sort_order: str = "desc"
    date_from: date | None = None
    date_to: date | None = None
    ledger_type: tuple[str, ...] = ()
    allocation_status: tuple[str, ...] = ()
    currency_code: tuple[str, ...] = ()
    q: str = ""
    tag: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class TargetLedgerTagVO:
    view_id: int
    view_name: str
    view_system_name: str
    tag_id: int
    tag_name: str
    tag_system_name: str


@dataclass(frozen=True, slots=True)
class TargetLedgerEntryVO:
    id: int
    ledger_type: str
    allocation_status: str
    title: str
    start_time: datetime
    end_time: datetime
    in_amount_value: int
    in_amount_scale: int
    in_currency_code: str
    out_amount_value: int
    out_amount_scale: int
    out_currency_code: str
    in_account_code: str
    out_account_code: str
    projection_version: int
    tags: tuple[TargetLedgerTagVO, ...] = ()


@dataclass(frozen=True, slots=True)
class TargetLedgerPageVO:
    items: tuple[TargetLedgerEntryVO, ...]
    total: int
    page: int
    page_size: int
    filters: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TargetImportFileVO:
    id: int
    source_type: str
    institution_code: str
    filename: str
    file_format: str
    sha256: str
    period_start: str
    period_end: str
    status: str


@dataclass(frozen=True, slots=True)
class TargetRawEvidenceVO:
    id: int
    bill_id: int
    import_file_id: int
    source_row_number: int
    source_reference: str
    raw_payload: str
    raw_hash: str
    parse_status: str
    issue_code: str
    issue_message: str


@dataclass(frozen=True, slots=True)
class TargetFactEvidenceVO:
    id: int
    fact_key: str
    occurred_time: datetime
    cash_direction: str
    amount_value: int
    amount_scale: int
    currency_code: str
    account_code: str
    counterparty: str
    summary: str


@dataclass(frozen=True, slots=True)
class TargetReviewLineVO:
    id: int
    case_id: int
    bill_id: int
    role: str
    party: str
    amount_value: int
    amount_scale: int
    currency_code: str


@dataclass(frozen=True, slots=True)
class TargetReviewHistoryVO:
    id: int
    case_id: int
    version: int
    operation: str
    schema_version: int
    request_json: str
    before_json: str
    after_json: str
    snapshot_hash: str
    reverses_history_id: int
    actor: str
    reason: str
    idempotency_key: str
    created_time: datetime


@dataclass(frozen=True, slots=True)
class TargetReviewEvidenceVO:
    id: int
    review_type: str
    status: str
    allocation_status: str
    version: int
    title: str
    result_json: str
    is_projection_source: bool
    lines: tuple[TargetReviewLineVO, ...] = ()
    history: tuple[TargetReviewHistoryVO, ...] = ()


@dataclass(frozen=True, slots=True)
class TargetLedgerDetailVO:
    entry: TargetLedgerEntryVO
    facts: tuple[TargetFactEvidenceVO, ...]
    raw_evidence: tuple[TargetRawEvidenceVO, ...]
    import_files: tuple[TargetImportFileVO, ...]
    reviews: tuple[TargetReviewEvidenceVO, ...]


@dataclass(frozen=True, slots=True)
class TargetLedgerReadinessVO:
    legacy_bill_count: int
    fact_count: int
    fact_source_count: int
    confirmed_review_count: int
    review_source_count: int
    ledger_entry_count: int
    active_tag_view_count: int
    ledger_tag_count: int


@dataclass(frozen=True, slots=True)
class TargetLedgerAggregateVO:
    day: str
    ledger_type: str
    allocation_status: str
    direction: str
    currency_code: str
    amount_scale: int
    amount_value: int
    entry_count: int
    nettable: bool


class TargetCurrencySummaryRead(BaseModel):
    currency_code: str
    amount_scale: int
    income_value: int
    expense_value: int
    refund_offset_value: int
    net_value: int


class TargetLedgerActivityRead(BaseModel):
    ledger_type: str
    currency_code: str
    amount_scale: int
    in_amount_value: int
    out_amount_value: int
    nettable: bool


class TargetLedgerTrendRead(TargetCurrencySummaryRead):
    day: str


class TargetLedgerSummaryRead(BaseModel):
    entry_count: int
    provisional_count: int
    totals: list[TargetCurrencySummaryRead]
    activities: list[TargetLedgerActivityRead]
    trend: list[TargetLedgerTrendRead]
    basis_version: str = "target-ledger-v1"


class TargetMoneyRead(BaseModel):
    amount_value: int
    amount_scale: int
    currency_code: str


class TargetLedgerTagRead(BaseModel):
    view_id: int
    view_name: str
    view_system_name: str
    tag_id: int
    tag_name: str
    tag_system_name: str


class TargetLedgerEntryRead(BaseModel):
    id: int
    ledger_type: str
    allocation_status: str
    title: str
    start_time: datetime
    end_time: datetime
    incoming: TargetMoneyRead
    outgoing: TargetMoneyRead
    in_account_code: str
    out_account_code: str
    projection_version: int
    tags: list[TargetLedgerTagRead]


class TargetLedgerPageRead(BaseModel):
    items: list[TargetLedgerEntryRead]
    total: int
    page: int
    page_size: int
    filters: dict[str, object]


class TargetImportFileRead(BaseModel):
    id: int
    source_type: str
    institution_code: str
    filename: str
    file_format: str
    sha256: str
    period_start: str
    period_end: str
    status: str


class TargetRawEvidenceRead(BaseModel):
    id: int
    bill_id: int
    import_file_id: int
    source_row_number: int
    source_reference: str
    raw_payload: dict[str, Any] | list[Any] | str | int | float | bool | None
    raw_hash: str
    parse_status: str
    issue_code: str
    issue_message: str


class TargetFactEvidenceRead(BaseModel):
    id: int
    fact_key: str
    occurred_time: datetime
    cash_direction: str
    amount: TargetMoneyRead
    account_code: str
    counterparty: str
    summary: str


class TargetReviewLineRead(BaseModel):
    id: int
    bill_id: int
    role: str
    party: str
    amount: TargetMoneyRead


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


class TargetReviewEvidenceRead(BaseModel):
    id: int
    review_type: str
    status: str
    allocation_status: str
    version: int
    title: str
    result: dict[str, Any]
    is_projection_source: bool
    lines: list[TargetReviewLineRead]
    history: list[TargetReviewHistoryRead]


class TargetLedgerDetailRead(BaseModel):
    entry: TargetLedgerEntryRead
    facts: list[TargetFactEvidenceRead]
    raw_evidence: list[TargetRawEvidenceRead]
    import_files: list[TargetImportFileRead]
    reviews: list[TargetReviewEvidenceRead]


class LedgerSummaryBasisRead(BaseModel):
    income_value: int
    expense_value: int
    refund_offset_value: int
    net_value: int


class LedgerSummaryShadowComparisonRead(BaseModel):
    matched: bool
    comparable: bool
    legacy_entry_count: int
    target_entry_count: int
    legacy: LedgerSummaryBasisRead
    target: LedgerSummaryBasisRead
    differences: list[str]


class TargetLedgerShadowStatusRead(BaseModel):
    ready: bool
    legacy_bill_count: int
    fact_count: int
    fact_source_count: int
    confirmed_review_count: int
    review_source_count: int
    ledger_entry_count: int
    active_tag_view_count: int
    ledger_tag_count: int
    summary: LedgerSummaryShadowComparisonRead
    blockers: list[str]
