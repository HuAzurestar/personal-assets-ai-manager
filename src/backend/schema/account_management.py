"""Account metadata commands: identity is immutable and money is never moved."""
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import Field, StrictInt, field_validator
from backend.schema.review_command import Intent, PositiveId, NonnegativeId

AccountStatus = Literal["ACTIVE", "CLOSED"]
Interval = Annotated[StrictInt, Field(ge=0, le=120)]


class PartyCreate(Intent):
    name: str = Field(min_length=1, max_length=120)

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("name cannot be blank")
        return value


class ExpectedMetadata(Intent):
    expected_updated_time: datetime

    @field_validator("expected_updated_time")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expected_updated_time requires timezone")
        return value.astimezone(timezone.utc)


class PartyUpdate(PartyCreate, ExpectedMetadata):
    status: AccountStatus


class AccountCreate(PartyCreate):
    party_id: PositiveId
    statement_interval_months: Interval = 0
    snapshot_interval_months: Interval = 0


class AccountUpdate(PartyCreate, ExpectedMetadata):
    status: AccountStatus
    statement_interval_months: Interval = 0
    snapshot_interval_months: Interval = 0


class RefCreate(Intent):
    account_id: NonnegativeId = 0
    name: str = Field(default="", max_length=120)
    institution: str = Field(default="", max_length=120)
    reference: str = Field(default="", max_length=200)


class RefUpdate(RefCreate, ExpectedMetadata):
    status: AccountStatus
    # Direct metadata writes cannot change parent; only move-preview/command can.


class RefMove(ExpectedMetadata):
    account_id: NonnegativeId


class RefMoveCommand(RefMove):
    preview_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class PartyPO(Intent):
    id: PositiveId
    name: str
    status: AccountStatus
    created_time: datetime
    updated_time: datetime


class AccountPO(PartyPO):
    party_id: PositiveId
    statement_interval_months: int
    snapshot_interval_months: int


class AccountRefPO(Intent):
    id: PositiveId
    account_id: NonnegativeId
    name: str
    institution: str
    reference: str
    source_namespace: str
    source_identity: str
    identity_strength: Literal["UNKNOWN", "RELIABLE", "WEAK"]
    status: AccountStatus
    created_time: datetime
    updated_time: datetime
    latest_source_time: datetime | None = None


class MovePreviewPO(Intent):
    ref_id: PositiveId
    from_account_id: NonnegativeId
    to_account_id: NonnegativeId
    from_party_id: NonnegativeId
    to_party_id: NonnegativeId
    cross_party: bool
    affected_ledger_count: int
    preview_digest: str


class PartyResponse(Intent):
    status: int
    message: str
    body: PartyPO


class AccountResponse(Intent):
    status: int
    message: str
    body: AccountPO


class RefResponse(Intent):
    status: int
    message: str
    body: AccountRefPO


class MovePreviewResponse(Intent):
    status: int
    message: str
    body: MovePreviewPO


class PartyListPO(Intent):
    items: list[PartyPO]
    total: int
    page_index: int
    page_size: int


class AccountListPO(Intent):
    items: list[AccountPO]
    total: int
    page_index: int
    page_size: int


class RefListPO(Intent):
    items: list[AccountRefPO]
    total: int
    page_index: int
    page_size: int


class PartyListResponse(Intent):
    status: int
    message: str
    body: PartyListPO


class AccountListResponse(Intent):
    status: int
    message: str
    body: AccountListPO


class RefListResponse(Intent):
    status: int
    message: str
    body: RefListPO
