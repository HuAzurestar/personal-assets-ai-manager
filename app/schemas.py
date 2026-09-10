from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.money import cents


class BillCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    occurred_at: datetime
    merchant: str = Field(min_length=1, max_length=200)
    note: str = ""
    amount: float
    currency: str = Field(default="CNY", pattern="^CNY$")
    account_name: str = Field(default="手工未提供账户", min_length=1, max_length=120)

    @field_validator("amount")
    @classmethod
    def exact_amount(cls, value):
        cents(value)
        return value


class BillRead(BillCreate):
    id: int
    category: str
    tags: list[str]
    source_type: str | None = None
    source_reference: str | None = None
    import_batch_id: int | None = None
    direction: str
    aggregate_excluded: bool
    transfer_group_id: str | None = None
    duplicate_of_id: int | None = None
    view_tags: list["ViewTagAssignmentRead"] = []
    tag_state: dict[str, str] = Field(default_factory=dict)
    tag_revision_id: int = 0


class AssetCreate(BaseModel):
    account_name: str = Field(min_length=1, max_length=120)
    account_type: str = Field(min_length=1, max_length=80)
    balance: float
    recorded_at: datetime


class AssetRead(AssetCreate):
    id: int


class TagRequest(BaseModel):
    merchant: str
    note: str = ""


class TagResult(BaseModel):
    category: str
    tags: list[str]
    provider: str


class TagApply(BaseModel):
    strategy: str = Field(pattern="^(local_rules|llm_suggestion|manual|authorised_auto)$")
    category: str | None = None
    tags: list[str] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    reason: str = Field(default="", max_length=500)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=120)


class UndoRequest(BaseModel):
    reason: str = Field(default="", max_length=500)


class IssueResolve(BillCreate):
    reason: str = Field(min_length=1, max_length=500)


class NatureRequest(BaseModel):
    nature: str = Field(pattern="^(refund|ordinary)$")
    reason: str = Field(min_length=1, max_length=500)
    expected_audit_id: int = Field(default=0, ge=0)


class TagAuditRead(BaseModel):
    id: int
    category: str
    tags: list[str]
    strategy: str
    confidence: float
    provider: str
    superseded: bool
    action: str = "confirm"
    actor: str = "local-user"
    reason: str = ""
    reverses_audit_id: int | None = None
    undone: bool = False
    undone_at: datetime | None = None
    created_at: datetime
    tag_state: dict[str, str] = Field(default_factory=dict)


class ImportBatchRead(BaseModel):
    id: int
    source_type: str
    filename: str
    imported_at: datetime
    row_count: int
    imported_count: int
    candidate_count: int
    issue_count: int = 0
    file_sha256: str | None = None
    file_format: str | None = None
    archive_entry: str | None = None
    batch_token: str | None = None


class ImportPreviewRead(BaseModel):
    source_type: str
    filename: str
    file_format: str
    archive_entry: str | None = None
    file_sha256: str
    row_count: int
    columns: list[str]
    preview_rows: list[dict[str, str]]
    valid_count: int = 0
    issues: list[dict] = Field(default_factory=list)


class BatchFilePayload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1)


class BatchPreviewItemRead(BaseModel):
    filename: str
    ok: bool
    duplicate: bool = False
    error: str | None = None
    preview: ImportPreviewRead | None = None


class BatchPreviewRead(BaseModel):
    batch_token: str
    files: list[BatchPreviewItemRead]


class BatchImportItemRead(BaseModel):
    filename: str
    status: str
    error: str | None = None
    import_batch: ImportBatchRead | None = None


class BatchImportRequest(BaseModel):
    files: list[BatchFilePayload] = Field(min_length=1, max_length=100)
    batch_token: str | None = Field(default=None, max_length=64)


class BatchImportRead(BaseModel):
    batch_token: str
    files: list[BatchImportItemRead]


class ReviewCandidateRead(BaseModel):
    id: int
    current_action_id: int = 0
    candidate_type: str
    confidence: float
    reason: str
    status: str
    member_bills: list[BillRead] = Field(default_factory=list)
    transfer_group_id: str | None = None
    transfer_kind: str | None = None
    retained_bill_id: int | None = None
    resolved_at: datetime | None = None
    undo_available: bool = False
    aggregation_effect: str
    created_at: datetime
    bill: BillRead
    related_bill: BillRead


class CandidateDecision(BaseModel):
    action: str = Field(pattern="^(confirm_transfer|confirm_personal_transfer|confirm_third_party_transfer|resolve_duplicate|reject_duplicate|ignored|deferred)$")
    retained_bill_id: int | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=120)
    expected_action_id: int | None = None
    expected_member_ids: list[int] | None = None


class CandidateBatchItem(BaseModel):
    candidate_id: int
    action: str = Field(pattern="^(confirm_transfer|confirm_personal_transfer|confirm_third_party_transfer|resolve_duplicate|reject_duplicate|ignored|deferred)$")
    retained_bill_id: int | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=120)
    expected_action_id: int | None = None
    expected_member_ids: list[int] | None = None


class CandidateBatchDecision(BaseModel):
    items: list[CandidateBatchItem] = Field(min_length=1, max_length=100)


class CandidatePageRead(BaseModel):
    items: list[ReviewCandidateRead]
    total: int
    page: int
    page_size: int


class ViewTagRead(BaseModel):
    id: int
    name: str
    system_name: str
    is_unclassified: bool
    archived: bool


class TagViewRead(BaseModel):
    id: int
    name: str
    system_name: str
    archived: bool
    tags: list[ViewTagRead]


class TagViewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    system_name: str | None = Field(default=None, pattern="^[a-z][a-z0-9_]{0,63}$")


class TagViewUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None


class ViewTagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    system_name: str | None = Field(default=None, pattern="^[a-z][a-z0-9_]{0,63}$")


class ViewTagUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None
    migrate_to_tag_id: int | None = None


class ViewTagAssignmentRead(BaseModel):
    view_id: int
    view_name: str
    tag_id: int
    tag_name: str
    view_system_name: str
    tag_system_name: str
    strategy: str
    confidence: float


class ViewTagAssignmentRequest(BaseModel):
    tag_id: int
    strategy: str = Field(default="manual", max_length=60)
    confidence: float = Field(default=0.95, ge=0, le=1)


class TagStateAssignmentRequest(BaseModel):
    tag_state: dict[str, str] = Field(default_factory=dict)
    strategy: str = Field(default="manual", pattern="^(manual|local_rules|llm_suggestion|authorised_auto)$")
    confidence: float = Field(default=0.95, ge=0, le=1)
    expected_audit_id: int | None = None
    reason: str = Field(default="", max_length=500)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=120)


class TagStateBulkAssignmentRequest(TagStateAssignmentRequest):
    bill_ids: list[int] = Field(min_length=1, max_length=100)
    merge: bool = False
    expected_revisions: dict[int, int] | None = None


class TransactionPageRead(BaseModel):
    items: list[BillRead]
    total: int
    page: int
    page_size: int
    filters: dict[str, object]
    sort: dict[str, str]


class AccountRevisionRequest(BaseModel):
    account_name: str = Field(min_length=1, max_length=120)
    reason: str = Field(default="", max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=120)


class RefundAllocationCreate(BaseModel):
    refund_bill_id: int
    expense_bill_id: int
    amount: float = Field(gt=0)
    reason: str = Field(default="", max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=120)

    @field_validator("amount")
    @classmethod
    def exact_amount(cls, value):
        cents(value)
        return value


class RefundAllocationRead(BaseModel):
    id: int
    refund_bill_id: int
    expense_bill_id: int
    amount: float
    status: str
    idempotency_key: str
    created_at: datetime
    revoked_at: datetime | None = None


class TrendPointRead(BaseModel):
    day: date
    income: float
    spending: float
    net: float
    bill_count: int
