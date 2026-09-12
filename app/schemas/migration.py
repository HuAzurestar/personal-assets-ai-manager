from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class LegacyBillVO:
    id: int
    occurred_at: datetime
    merchant: str
    note: str
    amount: float
    currency: str
    account_name: str


@dataclass(frozen=True, slots=True)
class LegacyImportBatchVO:
    id: int
    source_type: str
    filename: str
    imported_at: datetime
    row_count: int
    imported_count: int
    batch_token: str | None


@dataclass(frozen=True, slots=True)
class LegacyImportArtifactVO:
    id: int
    import_batch_id: int
    source_type: str
    filename: str
    file_format: str
    sha256: str


@dataclass(frozen=True, slots=True)
class LegacyOriginVO:
    id: int
    bill_id: int
    source_type: str
    source_reference: str
    raw_payload: str
    import_batch_id: int | None
    source_row_number: int | None


@dataclass(frozen=True, slots=True)
class LegacyImportIssueVO:
    id: int
    import_batch_id: int
    source_row_number: int
    raw_payload: str
    error: str
    resolution: str
    bill_id: int | None
    resolved_at: datetime | None
    created_at: datetime | None


@dataclass(frozen=True, slots=True)
class LegacyImportIssueActionVO:
    id: int
    issue_id: int
    action: str
    payload: str
    actor: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ImportFileShadowVO:
    id: int
    batch_code: str
    source_type: str
    institution_code: str
    filename: str
    file_format: str
    sha256: str
    period_start: str
    period_end: str
    total_count: int
    success_count: int
    skip_count: int
    issue_count: int
    status: str


@dataclass(frozen=True, slots=True)
class BillRawShadowVO:
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
class BillFactShadowVO:
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


class ShadowTableReport(BaseModel):
    expected_count: int
    actual_count: int
    inserted_count: int
    expected_digest: str
    actual_digest: str
    mismatched_ids: list[int]


class FactShadowReport(BaseModel):
    matched: bool
    import_file: ShadowTableReport
    bill_raw: ShadowTableReport
    bill_fact: ShadowTableReport
    blockers: list[str]


@dataclass(frozen=True, slots=True)
class LegacyMatterVO:
    id: int
    version: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LegacyMatterRevisionVO:
    id: int
    matter_id: int
    version: int
    action: str
    snapshot: str
    reason: str
    actor: str
    idempotency_key: str
    request_payload: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewCaseShadowVO:
    id: int
    created_time: datetime
    updated_time: datetime
    review_type: str
    status: str
    allocation_status: str
    version: int
    title: str
    result_json: str


@dataclass(frozen=True, slots=True)
class ReviewCaseBillShadowVO:
    id: int
    created_time: datetime
    updated_time: datetime
    case_id: int
    bill_id: int
    role: str
    party: str
    amount_value: int
    amount_scale: int
    currency_code: str


@dataclass(frozen=True, slots=True)
class ReviewHistoryShadowVO:
    id: int
    created_time: datetime
    updated_time: datetime
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


class ReviewShadowReport(BaseModel):
    matched: bool
    review_case: ShadowTableReport
    review_case_bill: ShadowTableReport
    review_history: ShadowTableReport
    blockers: list[str]


@dataclass(frozen=True, slots=True)
class LegacyCandidateVO:
    id: int
    candidate_type: str
    bill_id: int
    related_bill_id: int
    member_bill_ids: str
    confidence: float
    reason: str
    status: str
    group_fingerprint: str
    superseded_by_id: int | None
    transfer_group_id: str | None
    transfer_kind: str | None
    retained_bill_id: int | None
    resolved_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LegacyCandidateActionVO:
    id: int
    candidate_id: int
    action: str
    before_state: str
    after_state: str
    actor: str
    reason: str
    reverses_action_id: int | None
    idempotency_key: str | None
    request_payload: str
    created_at: datetime
    undone: bool
    undone_at: datetime | None


@dataclass(frozen=True, slots=True)
class LegacyRefundAllocationVO:
    id: int
    refund_bill_id: int
    expense_bill_id: int
    amount: float
    status: str
    idempotency_key: str
    request_payload: str
    created_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class LegacyRefundAllocationAuditVO:
    id: int
    allocation_id: int
    action: str
    actor: str
    reason: str
    before_state: str
    after_state: str
    reverses_audit_id: int | None
    idempotency_key: str | None
    request_payload: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LegacyRefundDesignationVO:
    bill_id: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LegacyRefundNatureAuditVO:
    id: int
    bill_id: int
    action: str
    reason: str
    actor: str
    before_nature: str
    after_nature: str
    idempotency_key: str | None
    request_payload: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LegacyAccountRevisionVO:
    id: int
    bill_id: int
    before_account: str
    after_account: str
    action: str
    actor: str
    reason: str
    reverses_revision_id: int | None
    undone: bool
    undone_at: datetime | None
    idempotency_key: str | None
    request_payload: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LegacyAccountBillVO:
    id: int
    account_name: str


@dataclass(frozen=True, slots=True)
class LegacyTagAuditVO:
    id: int
    bill_id: int
    category: str
    tags: str
    tag_state_json: str
    strategy: str
    confidence: float
    provider: str
    superseded: bool
    action: str
    actor: str
    reason: str
    before_state_json: str
    before_category: str
    reverses_audit_id: int | None
    undone: bool
    undone_at: datetime | None
    idempotency_key: str | None
    request_payload: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LegacyTagBillVO:
    id: int
    category: str
    tag_state_json: str


@dataclass(frozen=True, slots=True)
class LegacyTagViewVO:
    id: int
    name: str
    system_name: str
    archived: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LegacyTagValueVO:
    id: int
    view_id: int
    name: str
    system_name: str
    is_unclassified: bool
    archived: bool


@dataclass(frozen=True, slots=True)
class TargetTagViewShadowVO:
    id: int
    created_time: datetime
    updated_time: datetime
    name: str
    system_name: str
    status: str


@dataclass(frozen=True, slots=True)
class TargetTagShadowVO:
    id: int
    created_time: datetime
    updated_time: datetime
    view_id: int
    name: str
    system_name: str
    status: str


class TagDictionaryShadowReport(BaseModel):
    matched: bool
    tag_view: ShadowTableReport
    tag: ShadowTableReport
    blockers: list[str]


@dataclass(frozen=True, slots=True)
class ProjectionFactVO:
    id: int
    created_time: datetime
    updated_time: datetime
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
class ProjectionReviewCaseVO:
    id: int
    created_time: datetime
    updated_time: datetime
    review_type: str
    status: str
    allocation_status: str
    version: int
    title: str
    result_json: str


@dataclass(frozen=True, slots=True)
class ProjectionReviewLineVO:
    id: int
    case_id: int
    bill_id: int
    role: str
    party: str
    amount_value: int
    amount_scale: int
    currency_code: str


@dataclass(frozen=True, slots=True)
class LedgerEntryShadowVO:
    id: int
    created_time: datetime
    updated_time: datetime
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
    input_hash: str
    projection_version: int


@dataclass(frozen=True, slots=True)
class LedgerEntrySourceShadowVO:
    id: int
    created_time: datetime
    updated_time: datetime
    ledger_id: int
    source_kind: str
    source_id: int


@dataclass(frozen=True, slots=True)
class LedgerEntryTagShadowVO:
    id: int
    created_time: datetime
    updated_time: datetime
    ledger_id: int
    tag_id: int


class LedgerProjectionShadowReport(BaseModel):
    matched: bool
    ledger_entry: ShadowTableReport
    ledger_entry_source: ShadowTableReport
    ledger_entry_tag: ShadowTableReport
    blockers: list[str]
