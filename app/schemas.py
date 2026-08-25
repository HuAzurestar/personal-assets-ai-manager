from datetime import datetime

from pydantic import BaseModel, Field


class BillCreate(BaseModel):
    occurred_at: datetime
    merchant: str = Field(min_length=1, max_length=200)
    note: str = ""
    amount: float


class BillRead(BillCreate):
    id: int
    category: str
    tags: list[str]
    source_type: str | None = None
    source_reference: str | None = None
    import_batch_id: int | None = None


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


class TagAuditRead(BaseModel):
    id: int
    category: str
    tags: list[str]
    strategy: str
    confidence: float
    provider: str
    superseded: bool
    created_at: datetime


class ImportBatchRead(BaseModel):
    id: int
    source_type: str
    filename: str
    imported_at: datetime
    row_count: int
    imported_count: int
    candidate_count: int
    file_sha256: str | None = None
    file_format: str | None = None
    archive_entry: str | None = None


class ImportPreviewRead(BaseModel):
    source_type: str
    filename: str
    file_format: str
    archive_entry: str | None = None
    file_sha256: str
    row_count: int
    columns: list[str]
    mapping: dict[str, str | None]
    preview_rows: list[dict[str, str]]


class ReviewCandidateRead(BaseModel):
    id: int
    candidate_type: str
    confidence: float
    reason: str
    status: str
    created_at: datetime
    bill: BillRead
    related_bill: BillRead


class CandidateDecision(BaseModel):
    status: str = Field(pattern="^(confirmed|ignored)$")
