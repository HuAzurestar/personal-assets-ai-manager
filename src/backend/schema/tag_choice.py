"""Flat public tag choices; no nested dictionary or ledger assignments."""
from datetime import datetime
from typing import Literal

from backend.schema.review_command import Intent, PositiveId
from backend.schema.list_query import ListRequest
from backend.schema.bounded_search import SearchRequest
from backend.schema.response import ListResponse, SuccessResponse


class TagChoicePO(Intent):
    id: PositiveId
    name: str
    system_name: str
    status: Literal["ACTIVE", "ARCHIVED"]
    view_id: PositiveId
    view_name: str
    view_status: Literal["ACTIVE", "ARCHIVED"]
    display_label: str
    created_time: datetime
    updated_time: datetime


class TagChoiceListRequest(ListRequest):
    pass


class TagChoiceSearchRequest(SearchRequest):
    pass


class TagChoiceSearchPO(Intent):
    items: list[TagChoicePO]
    total: None = None
    page_size: int
    next_cursor: str | None
    has_more: bool
    scanned_count: int
    elapsed_ms: float


class TagChoiceResponse(SuccessResponse[TagChoicePO]):
    pass


class TagChoiceListResponse(ListResponse[TagChoicePO]):
    pass


class TagChoiceSearchResponse(SuccessResponse[TagChoiceSearchPO]):
    pass
