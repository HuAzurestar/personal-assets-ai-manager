from datetime import datetime
from typing import Literal
from pydantic import Field, model_validator
from backend.schema.import_command import ImportInput, RowIdentity, EvidenceTarget
from backend.schema.identifier import PositiveId
from backend.schema.review_read import PO
from backend.schema.response import SuccessResponse
from backend.schema.list_query import ListRequest
from backend.schema.bounded_search import SearchRequest
from backend.schema.import_file import ImportFileRead
from backend.schema.import_batch_read import ImportProgress
from backend.schema.transaction_fact import TransactionFactRead


class SourceFileListRequest(ListRequest):
    pass


class SourceFileSearchRequest(SearchRequest):
    pass


class SourceRowListRequest(ListRequest):
    pass


class SourceRowSearchRequest(SearchRequest):
    pass


class SourceRowPO(PO):
    id: int
    transaction_import_file_id: int
    source_row_number: int
    transaction_id: int
    row_status: Literal[0, 1, 2, 3]
    source_reference: str
    raw_hash: str
    issue_code: str
    issue_message: str
    created_time: datetime
    updated_time: datetime


class SourceFileDetailPO(PO):
    file: ImportFileRead
    progress: ImportProgress
    coverage: Literal["ACTIVITY_RANGE_ONLY"]


class SourceFileListPO(PO):
    items: list[ImportFileRead] = Field(max_length=100)
    total: int
    page_index: int
    page_size: int


class SourceRowListPO(PO):
    items: list[SourceRowPO] = Field(max_length=100)
    total: int
    page_index: int
    page_size: int


class SourceFileSearchPO(PO):
    items: list[ImportFileRead] = Field(max_length=100)
    total: None = None
    page_size: int
    next_cursor: str | None
    has_more: bool
    scanned_count: int
    elapsed_ms: float


class SourceRowSearchPO(SourceFileSearchPO):
    items: list[SourceRowPO] = Field(max_length=100)


class SourceRowDetailPO(PO):
    row: SourceRowPO
    raw_payload: dict | None
    fact: TransactionFactRead | None


class SourceRelationPO(PO):
    row_id: int
    source_row_number: int
    transaction_id: int
    allocation_id: int | None
    review_id: int | None
    ledger_id: int | None
    review_status: Literal["CONFIRMED", "REVOKED"] | None
    account_ref_id: int | None
    cash_amount: int | None
    cash_currency_code: str | None


class SourceRelationsPO(PO):
    items: list[SourceRelationPO] = Field(max_length=4000)
    total: int


class SourceFileListResponse(SuccessResponse[SourceFileListPO]):
    pass


class SourceFileSearchResponse(SuccessResponse[SourceFileSearchPO]):
    pass


class SourceFileDetailResponse(SuccessResponse[SourceFileDetailPO]):
    pass


class SourceRowListResponse(SuccessResponse[SourceRowListPO]):
    pass


class SourceRowSearchResponse(SuccessResponse[SourceRowSearchPO]):
    pass


class SourceRowDetailResponse(SuccessResponse[SourceRowDetailPO]):
    pass


class SourceRelationsResponse(SuccessResponse[SourceRelationsPO]):
    pass


class SourceReconcileRow(RowIdentity):
    resolution: Literal["AUTO", "NEW", "LINK_EXISTING", "DUPLICATE"] = "AUTO"
    decision: Literal["ACCEPT", "SKIP"] | None = None
    target: EvidenceTarget | None = None

    @model_validator(mode="after")
    def pair_context(self):
        if self.target is not None and self.resolution not in {"LINK_EXISTING", "DUPLICATE"}:
            raise ValueError("only a pair can carry a target")
        # Lost pair context is a truthful unresolved read, not an invented target.
        return self


class SourceFileProof(ImportInput):
    file_id: PositiveId
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SourceReconcileInput(ImportInput):
    rows: list[SourceReconcileRow] = Field(min_length=1, max_length=1000)
    files: list[SourceFileProof] = Field(max_length=100)

    @model_validator(mode="after")
    def distinct(self):
        if len({(row.file_id,row.source_row_number) for row in self.rows}) != len(self.rows) or \
            len({file.file_id for file in self.files}) != len(self.files):
            raise ValueError("reconciliation requires unique exact source locators")
        return self


class ReconcileFact(PO):
    id: int
    amount: int
    currency_code: str
    cash_direction: Literal["IN", "OUT"]
    occurred_time: datetime


class ReconcileOutput(PO):
    transaction_id: int
    allocation_id: int
    review_id: int
    review_status: Literal["CONFIRMED", "REVOKED"]
    ledger_id: int
    economic_type: Literal["TRANSACTION", "ACCOUNT_TRANSFER", "ASSET_LIABILITY", "DUPLICATE"]
    account_ref_id: int
    cash_amount: int
    cash_currency_code: str
    cash_direction: Literal["IN", "OUT"]
    occurred_time: datetime


class ReconcileRowPO(PO):
    row: RowIdentity
    resolution: Literal["AUTO", "NEW", "LINK_EXISTING", "DUPLICATE"]
    row_id: int
    row_status: Literal[0, 1, 2, 3] | None
    transaction_id: int
    target_transaction_id: int
    state: Literal["NOT_PERSISTED", "UNPROCESSED", "SKIPPED", "INVALID", "ACCEPTED", "EVIDENCE_LINKED",
        "DUPLICATE_EXCLUDED", "CURRENT_STATE_CHANGED", "UNRESOLVED"]
    fully_observed: bool
    reason_codes: list[str]


class SourceReconcilePO(PO):
    observed_at: datetime
    items: list[ReconcileRowPO] = Field(max_length=1000)
    facts: list[ReconcileFact] = Field(max_length=2000)
    outputs: list[ReconcileOutput] = Field(max_length=4000)
    fully_observed: bool
    current_state_only: Literal[True]

    @model_validator(mode="after")
    def complete(self):
        if len({(item.row.file_id,item.row.source_row_number) for item in self.items}) != len(self.items) or \
            self.fully_observed != all(item.fully_observed for item in self.items):
            raise ValueError("current observation must describe every unique row")
        return self


class SourceReconcileResponse(SuccessResponse[SourceReconcilePO]):
    pass
