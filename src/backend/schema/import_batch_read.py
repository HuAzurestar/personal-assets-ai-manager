"""Typed public v1 batch import projections; never cache or replay results."""
from datetime import datetime
from typing import Literal
from pydantic import Field, StrictInt, model_validator
from backend.schema.review_read import PO, ReviewReadPO
from backend.schema.review_command import ExpectedReview
from backend.schema.import_command import RowChoice, RowIdentity, EvidenceTarget
from backend.schema.response import SuccessResponse, ListResponse
from backend.schema.candidate import CurrentReviewPO


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


class ImportDuplicateScope(PO):
    occurred_time: datetime | None
    currency_code: str | None
    cash_direction: Literal["IN", "OUT"] | None
    source_known: bool


class ImportDuplicateHint(PO):
    state: Literal["NONE_IN_SCOPE", "SUSPECTED", "UNCHECKED"]
    scope: ImportDuplicateScope
    candidate_count: StrictInt | None = Field(ge=0)
    reason_codes: list[str]

    @model_validator(mode="after")
    def truthful_count(self):
        valid = (self.state == "UNCHECKED" and self.candidate_count is None or
            self.state == "NONE_IN_SCOPE" and self.candidate_count == 0 and self.scope.source_known or
            self.state == "SUSPECTED" and self.candidate_count is not None and self.candidate_count > 0)
        if not valid:
            raise ValueError("risk state/count must describe the checked scope, not a fabricated zero")
        return self


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


class ImportMatchCandidatePO(PO):
    transaction_id: int
    occurred_time: datetime
    amount: int
    currency_code: str
    cash_direction: Literal["IN", "OUT"]
    summary_masked: str
    source_label_masked: str
    current_review_summaries: list[CurrentReviewPO] = Field(max_length=4000)
    eligible_actions: list[Literal["LINK_EXISTING", "DUPLICATE"]] = Field(max_length=2)
    reason_codes: list[str]


class ImportMatchListResponse(ListResponse[ImportMatchCandidatePO]):
    pass


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


class ImportBatchCounts(PO):
    new_real_fact: int
    new_duplicate_fact: int
    evidence_only: int
    skipped: int
    invalid: int
    unresolved: int


class ImportPairComparison(PO):
    occurred_time: datetime | None
    amount: int | None
    currency_code: str | None
    cash_direction: Literal["IN", "OUT"] | None
    exact_match: bool


class ImportPreviewPair(PO):
    row: RowIdentity
    target: EvidenceTarget | None
    resolution: Literal["AUTO", "NEW", "LINK_EXISTING", "DUPLICATE"]
    source_labels_masked: list[str] = Field(max_length=2)
    comparison: ImportPairComparison
    reason_codes: list[str]


class ImportCurrencyEffect(PO):
    currency_code: str
    cash_in_amount: int
    cash_out_amount: int
    excluded_in_amount: int
    excluded_out_amount: int


class ImportReviewStateEffect(PO):
    before: ReviewReadPO
    after_status: Literal["CONFIRMED", "REVOKED"]


class ImportPlannedDefault(PO):
    row: RowIdentity
    output_index: int
    type: Literal["NORMAL_TRANSACTION"]
    economic_type: Literal["TRANSACTION"]
    cash_direction: Literal["IN", "OUT"]
    amount: int
    currency_code: str
    occurred_time: datetime
    account_ref_id: int | None
    source_label_masked: str
    after_status: Literal["CONFIRMED", "REVOKED"]


class ImportPlannedDuplicateOutput(PO):
    row: RowIdentity
    output_index: int
    kept_target: EvidenceTarget
    economic_type: Literal["DUPLICATE"]
    cash_direction: Literal["IN", "OUT"]
    amount: int
    currency_code: str
    account_ref_id: int | None


class ImportPlannedDuplicateReview(PO):
    review_index: int
    type: Literal["OTHER_MANUAL"]
    case_code: Literal["DUPLICATE"]
    allocations: list[ImportPlannedDuplicateOutput] = Field(max_length=1000)
    revoke_original_defaults: list[RowIdentity] = Field(max_length=1000)


class ImportDefaultTag(PO):
    view_id: int
    tag_id: int
    view_name_masked: str
    tag_name_masked: str


class ImportTagEffect(PO):
    new_output_count: int
    affected_view_ids: list[int]
    default_assignments: list[ImportDefaultTag]
    projected_assignment_count: int
    affected_rule_ids: list[int]


class ImportBatchEffects(PO):
    by_currency: list[ImportCurrencyEffect]
    before_after_review_states: list[ImportReviewStateEffect] = Field(max_length=4000)
    new_original_defaults: list[ImportPlannedDefault] = Field(max_length=1000)
    new_duplicate_reviews: list[ImportPlannedDuplicateReview] = Field(max_length=100)
    tag_effect: ImportTagEffect


class ImportBatchBudget(PO):
    selected_rows: int
    review_groups: int
    facts: int
    outputs: int
    position_links: int
    tag_changes: int


class ImportBatchPreviewPO(PO):
    source_preview_digest: str
    batch_preview_digest: str
    selected_rows: list[RowIdentity] = Field(max_length=1000)
    expected_reviews: list[ExpectedReview] = Field(max_length=4000)
    counts: ImportBatchCounts
    pairs: list[ImportPreviewPair] = Field(max_length=1000)
    effects: ImportBatchEffects
    budget: ImportBatchBudget
    can_confirm: bool
    issues: list[PreviewIssue] = Field(max_length=1000)


class ImportBatchPreviewResponse(SuccessResponse[ImportBatchPreviewPO]):
    pass


class ImportCancelPO(PO):
    cancelled: bool


class ImportCancelResponse(SuccessResponse[ImportCancelPO]):
    pass
