from datetime import datetime
from typing import Literal
from backend.schema.review_read import PO
from backend.schema.response import SuccessResponse
from backend.schema.list_query import ListRequest
from backend.schema.bounded_search import SearchRequest


class CandidateListRequest(ListRequest):
    pass


class CandidateSearchRequest(SearchRequest):
    pass


class DefaultReviewPO(PO):
    review_id: int
    ledger_id: int


class CandidateCoveragePO(PO):
    state: Literal["FULL", "PARTIAL", "UNRESOLVED"]
    allocated_cash_amount: int
    remaining_cash_amount: int
    default_identity_state: Literal["KNOWN", "MISSING", "AMBIGUOUS"]
    account_identity_state: Literal["UNKNOWN", "KNOWN", "MULTIPLE"]


class CandidatePO(PO):
    transaction_id: int
    default_review: DefaultReviewPO | None
    occurred_time: datetime
    cash_direction: Literal["IN", "OUT"]
    cash_amount: int
    cash_currency_code: str
    summary: str
    counterparty: str
    coverage: CandidateCoveragePO
    account_ref_id: int


class CandidateListPO(PO):
    items: list[CandidatePO]
    total: int
    page_index: int
    page_size: int


class CandidateSearchPO(PO):
    items: list[CandidatePO]
    total: None = None
    page_size: int
    next_cursor: str | None
    has_more: bool
    scanned_count: int
    elapsed_ms: float


class CandidateListResponse(SuccessResponse[CandidateListPO]):
    pass


class CandidateSearchResponse(SuccessResponse[CandidateSearchPO]):
    pass
