"""Public business intent. Published IDs and cash facts are server-owned."""
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from backend.core.unit import unit_definition


class Intent(BaseModel):
    model_config = ConfigDict(extra="forbid")


PositiveId = Annotated[StrictInt, Field(gt=0)]
Amount = Annotated[StrictInt, Field(gt=0, le=9_000_000_000_000)]
NonnegativeId = Annotated[StrictInt, Field(ge=0)]
EconomicType = Literal["TRANSACTION", "ACCOUNT_TRANSFER", "ASSET_LIABILITY", "DUPLICATE"]
UsageScenario = Literal["GENERAL", "PERSONAL-LENDING", "SHARED-SETTLEMENT", "STORED-VALUE",
                        "DEPOSIT-PLEDGE", "REIMBURSEMENT", "CREDIT-CARD", "FORMAL-LOAN", "INVESTMENT"]


class PositionDraft(Intent):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    type: Literal["ASSET", "LIABILITY"]
    usage_scenario: UsageScenario
    party_id: PositiveId
    counterparty: str = Field(default="", max_length=200)
    unit_code: str = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def known_unit(self):
        self.unit_code = unit_definition(self.unit_code).code
        return self


class MoneySplitInput(Intent):
    transaction_id: PositiveId
    economic_type: EconomicType
    cash_amount: Amount
    account_ref_id: NonnegativeId


class LegDraft(Intent):
    existing_position_id: PositiveId | None = None
    new_position_index: Annotated[StrictInt, Field(ge=0)] | None = None
    type: Literal["OPENING", "MOVEMENT"]
    leg_amount: Amount
    leg_direction: Literal["IN", "OUT"]
    occurred_time: datetime
    source: NonnegativeId = 0
    basis: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def target_and_time(self):
        if (self.existing_position_id is None) == (self.new_position_index is None):
            raise ValueError("exactly one Position target is required")
        if self.occurred_time.tzinfo is None or self.occurred_time.utcoffset() is None:
            raise ValueError("occurred_time requires a timezone")
        self.occurred_time = self.occurred_time.astimezone(timezone.utc)
        return self


class PositionAllocationDraft(Intent):
    allocation_index: Annotated[StrictInt, Field(ge=0)]
    leg_index: Annotated[StrictInt, Field(ge=0)]
    cash_amount: Amount
    cash_currency_code: str = Field(min_length=1, max_length=12)


class AccountBinding(Intent):
    transaction_id: PositiveId
    account_ref_id: PositiveId


class DuplicateTransaction(Intent):
    transaction_id: PositiveId
    kept_transaction_id: PositiveId


class CaseParameters(Intent):
    """Minimal presets share explicit splits/objects; no accounting inference."""
    transaction_ids: list[PositiveId] = Field(default_factory=list, max_length=2000)
    allocations: list[MoneySplitInput] = Field(default_factory=list, max_length=4000)
    new_positions: list[PositionDraft] = Field(default_factory=list, max_length=4000)
    legs: list[LegDraft] = Field(default_factory=list, max_length=4000)
    position_allocations: list[PositionAllocationDraft] = Field(default_factory=list, max_length=4000)
    phase: Literal["ADVANCE_OUT", "COLLECT_IN", "RECEIVE_IN", "PAY_OUT"] | None = None


class CaseReviewInput(Intent):
    case_code: Literal["NORMAL", "REFUND", "SHARED_PAYMENT", "INTERNAL_TRANSFER", "BORROW_REPAY", "DUPLICATE"]
    title: str = Field(default="", max_length=160)
    parameters: CaseParameters
    account_bindings: list[AccountBinding] = Field(default_factory=list, max_length=2000)
    duplicate_transactions: list[DuplicateTransaction] = Field(default_factory=list, max_length=2000)


class PositionReviewInput(Intent):
    case_code: Literal["POS_OPENING", "POS_POSITION_OPEN", "POS_POSITION_SETTLE", "POS_CREDIT_PURCHASE", "POS_CREDIT_REPAY"]
    title: str = Field(default="", max_length=160)
    new_positions: list[PositionDraft] = Field(max_length=4000)
    allocations: list[MoneySplitInput] = Field(max_length=4000)
    legs: list[LegDraft] = Field(max_length=4000)
    position_allocations: list[PositionAllocationDraft] = Field(max_length=4000)
    account_bindings: list[AccountBinding] = Field(default_factory=list, max_length=2000)
    duplicate_transactions: list[DuplicateTransaction] = Field(default_factory=list, max_length=2000)


ReviewInput = Annotated[CaseReviewInput | PositionReviewInput, Field(discriminator="case_code")]


class ExpectedReview(Intent):
    review_id: PositiveId
    status: Literal["CONFIRMED", "REVOKED"]
    updated_time: datetime

    @model_validator(mode="after")
    def aware(self):
        if self.updated_time.tzinfo is None or self.updated_time.utcoffset() is None:
            raise ValueError("updated_time requires a timezone")
        self.updated_time = self.updated_time.astimezone(timezone.utc)
        return self


class ReviewChangeInput(Intent):
    deactivate_review_ids: list[PositiveId] = Field(default_factory=list, max_length=100)
    activate_review_ids: list[PositiveId] = Field(default_factory=list, max_length=100)
    new_reviews: list[ReviewInput] = Field(default_factory=list, max_length=100)
    expected_reviews: list[ExpectedReview] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unambiguous(self):
        for ids in (self.deactivate_review_ids, self.activate_review_ids,
                    [row.review_id for row in self.expected_reviews]):
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate Review ID")
        if set(self.deactivate_review_ids) & set(self.activate_review_ids):
            raise ValueError("contradictory activation")
        if not (self.deactivate_review_ids or self.activate_review_ids or self.new_reviews):
            raise ValueError("empty Review change")
        return self


class ReviewCommandInput(ReviewChangeInput):
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
