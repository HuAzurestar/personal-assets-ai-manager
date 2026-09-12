from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.schemas.ledger import LedgerBillVO


@dataclass(frozen=True, slots=True)
class ReviewPageQuery:
    page: int = 1
    page_size: int = 20
    status: str | None = None
    candidate_type: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewCandidateVO:
    id: int
    candidate_type: str
    confidence: float
    reason: str
    status: str
    transfer_group_id: str | None
    transfer_kind: str | None
    retained_bill_id: int | None
    resolved_at: datetime | None
    created_at: datetime
    current_action_id: int
    undo_available: bool
    member_bills: tuple[LedgerBillVO, ...]
    bill: LedgerBillVO
    related_bill: LedgerBillVO


@dataclass(frozen=True, slots=True)
class ReviewPageVO:
    items: tuple[ReviewCandidateVO, ...]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True, slots=True)
class MergeCandidateVO:
    id: int
    bill_id: int
    related_bill_id: int
    member_bill_ids: tuple[int, ...]
    group_fingerprint: str


@dataclass(frozen=True, slots=True)
class DuplicateBillVO:
    id: int
    occurred_at: datetime
    merchant: str
    amount: float


@dataclass(slots=True)
class ReviewCommandCandidateVO:
    id: int
    candidate_type: str
    bill_id: int
    related_bill_id: int
    member_bill_ids: tuple[int, ...]
    reason: str
    status: str
    transfer_group_id: str | None
    transfer_kind: str | None
    retained_bill_id: int | None
    resolved_at: datetime | None


@dataclass(slots=True)
class ReviewCommandBillVO:
    id: int
    occurred_at: datetime
    amount: float
    account_name: str
    aggregate_excluded: bool
    transfer_group_id: str | None
    duplicate_of_id: int | None


@dataclass(frozen=True, slots=True)
class ExistingActionVO:
    id: int
    candidate_id: int
    idempotency_key: str
    request_payload: str
    undone: bool


@dataclass(frozen=True, slots=True)
class CandidateActionWriteVO:
    candidate_id: int
    action: str
    before_state: str
    after_state: str
    actor: str
    reason: str
    idempotency_key: str | None
    request_payload: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CandidateSuggestionBillVO:
    id: int
    occurred_at: datetime
    merchant: str
    amount: float
    account_name: str


@dataclass(slots=True)
class PendingDuplicateSuggestionVO:
    id: int | None
    bill_id: int
    related_bill_id: int
    member_bill_ids: tuple[int, ...]
    reason: str


@dataclass(slots=True)
class CandidateSuggestionWriteVO:
    candidate_type: str
    bill_id: int
    related_bill_id: int
    member_bill_ids: str
    group_fingerprint: str
    confidence: float
    reason: str
    status: str
    created_at: datetime
