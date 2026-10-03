"""Typed public v1 batch import projections; never cache or replay results."""
from datetime import datetime
from typing import Literal
from pydantic import Field
from backend.schema.review_read import PO
from backend.schema.import_command import RowChoice
from backend.schema.response import SuccessResponse


class ActivityRange(PO):
    start: str | None
    end: str | None


class ImportProgress(PO):
    accepted: int
    skipped: int
    invalid: int
    remaining: int


class PreviewFile(ImportProgress):
    file_id: int
    sha256: str
    filename: str
    parsed_row_count: int
    activity_range: ActivityRange
    parse_status: Literal["READY", "EMPTY", "FAILED"]
    parse_issue_code: str
    parse_issue_message: str
    parse_recovery: str


class PreviewCounts(PO):
    new: int
    existing: int
    processed: int
    invalid: int
    ambiguous: int


class PreviewIssue(PO):
    file_id: int
    source_row_number: int
    code: str


class ImportPreviewPO(PO):
    token: str
    updated_time: datetime
    status: Literal["READY", "CONFIRMING"]
    timed_out: bool
    files: list[PreviewFile] = Field(max_length=100)
    counts: PreviewCounts
    preview_digest: str
    issues: list[PreviewIssue] = Field(max_length=100)
    issue_count: int
    has_more_issues: bool


class PreviewParsed(PO):
    occurred_time: datetime | None
    amount: int | None
    currency_code: str | None
    cash_direction: Literal["IN", "OUT"] | None
    summary: str


class PreviewAccountCandidate(PO):
    account_ref_id: int
    label_masked: str


class PreviewRowPO(PO):
    file_id: int
    source_row_number: int
    classification: Literal["NEW", "EXISTING", "PROCESSED", "INVALID", "AMBIGUOUS"]
    parsed: PreviewParsed
    existing_transaction_id: int
    persisted_row_status: int | None
    choice: RowChoice | None
    issue_codes: list[str]
    account_candidates: list[PreviewAccountCandidate] = Field(max_length=100)


class PreviewRowListPO(PO):
    items: list[PreviewRowPO] = Field(max_length=100)
    total: int
    page_index: int
    page_size: int


class ConfirmFile(ImportProgress):
    file_id: int
    sha256: str
    status: Literal[0, 1, 2, 3]
    updated_time: datetime


class ProcessedRow(PO):
    file_id: int
    source_row_number: int
    row_id: int
    row_status: Literal[1, 2, 3]
    transaction_id: int
    created_review_id: int
    created_ledger_id: int
    resolution_effect: Literal["EVIDENCE_ONLY", "NEW_REAL", "DUPLICATE_ZERO", "NONE"]
    effective_review_ids: list[int] = Field(max_length=4000)
    effective_ledger_ids: list[int] = Field(max_length=4000)
    duplicate_kept_transaction_id: int


class ImportConfirmPO(PO):
    files: list[ConfirmFile] = Field(max_length=100)
    processed_rows: list[ProcessedRow] = Field(max_length=1000)
    new_fact_count: int
    linked_existing_count: int
    manual_linked_count: int
    duplicate_fact_count: int
    skipped_count: int
    invalid_count: int
    remaining_count: int
    preview_updated_time: datetime


class ImportPreviewResponse(SuccessResponse[ImportPreviewPO]):
    pass


class PreviewRowListResponse(SuccessResponse[PreviewRowListPO]):
    pass


class ImportConfirmResponse(SuccessResponse[ImportConfirmPO]):
    pass


class ImportCancelPO(PO):
    cancelled: bool


class ImportCancelResponse(SuccessResponse[ImportCancelPO]):
    pass
