from datetime import datetime
from typing import Literal
from pydantic import Field
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
