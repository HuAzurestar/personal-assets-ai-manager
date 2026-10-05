from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schema.response import SuccessResponse
from backend.schema.review_command import EconomicType, ExpectedReview, UsageScenario


class PO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewPO(PO):
    id: int
    type: Literal["NORMAL_TRANSACTION", "BORROW_AND_REPAY", "CREDIT_CARD", "SHARED_SETTLEMENT", "OTHER_MANUAL"]
    status: Literal["CONFIRMED", "REVOKED"]
    title: str
    created_time: datetime
    updated_time: datetime


class FlowPO(PO):
    id: int
    economic_type: EconomicType
    cash_direction: Literal["IN", "OUT"]
    cash_amount: int
    cash_currency_code: str
    account_ref_id: int
    occurred_time: datetime
    created_time: datetime
    updated_time: datetime


class FirstAllocationPO(PO):
    id: int
    review_id: int
    transaction_id: int
    ledger_id: int
    cash_amount: int
    cash_currency_code: str
    created_time: datetime
    updated_time: datetime


class PositionPO(PO):
    id: int
    title: str
    description: str
    type: Literal["ASSET", "LIABILITY"]
    usage_scenario: UsageScenario
    party_id: int
    counterparty: str
    unit_code: str
    status: Literal["ACTIVE", "ARCHIVED", "SETTLED"]
    created_time: datetime
    updated_time: datetime


class PositionLegPO(PO):
    id: int
    position_id: int
    review_id: int
    type: Literal["OPENING", "MOVEMENT"]
    leg_amount: int
    leg_direction: Literal["IN", "OUT"]
    occurred_time: datetime
    source_position_leg_id: int
    basis: str
    unit_code: str
    created_time: datetime
    updated_time: datetime


class PositionAllocationPO(PO):
    id: int
    review_id: int
    ledger_id: int
    position_leg_id: int
    cash_amount: int
    cash_currency_code: str
    created_time: datetime
    updated_time: datetime


class ReviewReadPO(ReviewPO):
    allocations: list[FirstAllocationPO]
    ledger_entries: list[FlowPO]
    position_legs: list[PositionLegPO]
    position_allocations: list[PositionAllocationPO]
    positions: list[PositionPO]


class ReviewImpactPO(PO):
    conflicting_review_ids: list[int]
    restored_default_review_ids: list[int]
    affected_account_ref_ids: list[int]
    affected_position_ids: list[int]
    dependent_position_leg_ids: list[int]
    tag_ledger_ids: list[int]


class ReviewPreviewPO(PO):
    reviews: list[ReviewPO]
    new_reviews: list[dict] = Field(default_factory=list)
    position_changes: list[dict] = Field(default_factory=list)
    coverage: list[dict]
    impact: ReviewImpactPO
    blocking_issues: list[dict]
    expected_reviews: list[ExpectedReview]
    tag_effect: dict
    preview_digest: str


class ReviewCommandPO(PO):
    created_reviews: list[ReviewPO]
    created_positions: list[dict]
    review_states: list[ExpectedReview]
    coverage: list[dict]
    tag_effect: dict
    consumer_state: dict


class ReviewReadResponse(SuccessResponse[ReviewReadPO]):
    pass


class ReviewPreviewResponse(SuccessResponse[ReviewPreviewPO]):
    pass


class ReviewCommandResponse(SuccessResponse[ReviewCommandPO]):
    pass
