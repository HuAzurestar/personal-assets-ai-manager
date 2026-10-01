from datetime import datetime
from typing import Literal
from pydantic import Field, field_validator
from backend.schema.account_management import ExpectedMetadata
from backend.schema.review_command import UsageScenario
from backend.schema.review_read import PositionPO, PositionLegPO, PositionAllocationPO, PO, ReviewPO
from backend.schema.response import SuccessResponse


class PositionMetadata(ExpectedMetadata):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    usage_scenario: UsageScenario
    status: Literal["ACTIVE", "ARCHIVED", "SETTLED"]

    @field_validator("title")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("title cannot be blank")
        return value


class PositionDetailPO(PositionPO):
    quantity_state: Literal["UNKNOWN", "KNOWN", "NEEDS_REVIEW"]
    quantity: int | None
    cost_state: Literal["UNKNOWN", "NEEDS_REVIEW"]
    source_token: str


class PositionListPO(PO):
    items: list[PositionPO]
    total: int
    page_index: int
    page_size: int


class PositionSearchPO(PO):
    items: list[PositionPO]
    total: None = None
    page_size: int
    next_cursor: str | None
    has_more: bool
    scanned_count: int
    elapsed_ms: float


class PositionLegReadPO(PositionLegPO):
    review: ReviewPO
    position_allocations: list[PositionAllocationPO]


class PositionLegListPO(PO):
    items: list[PositionLegReadPO]
    total: int
    page_index: int
    page_size: int


class PositionResponse(SuccessResponse[PositionDetailPO]):
    pass


class PositionListResponse(SuccessResponse[PositionListPO]):
    pass


class PositionSearchResponse(SuccessResponse[PositionSearchPO]):
    pass


class PositionLegListResponse(SuccessResponse[PositionLegListPO]):
    pass
