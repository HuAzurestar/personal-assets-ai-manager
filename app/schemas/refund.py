from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel

from app.schemas import BillRead, RefundAllocationRead


class RefundNatureAuditRead(BaseModel):
    id: int
    action: str
    reason: str
    actor: str
    before_nature: str
    after_nature: str
    idempotency_key: str | None
    created_at: datetime


class RefundAllocationAuditRead(BaseModel):
    id: int
    action: str
    actor: str
    reason: str
    before_state: dict[str, object]
    after_state: dict[str, object]
    reverses_audit_id: int | None
    idempotency_key: str | None
    created_at: datetime


class RefundNatureRead(BaseModel):
    nature: str
    audit_id: int


class RefundNatureCommandRead(RefundNatureRead):
    bill_id: int


class RefundListItemRead(BaseModel):
    bill: BillRead
    unallocated: float
    allocations: list[RefundAllocationRead]
    nature_audit_id: int
    history: list[RefundNatureAuditRead]


class RefundBillSummaryRead(BaseModel):
    id: int
    occurred_at: datetime
    merchant: str
    amount: float
    currency: str
    account_name: str


class RefundSummaryRead(BaseModel):
    bill: RefundBillSummaryRead
    unallocated: float
    allocated: float
    allocation_count: int
    confirmed_allocation_count: int


class RefundPageRead(BaseModel):
    items: list[RefundSummaryRead]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True, slots=True)
class RefundAllocationVO:
    id: int
    refund_bill_id: int
    expense_bill_id: int
    amount: float
    status: str
    idempotency_key: str
    created_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class RefundAllocationCommandVO:
    id: int
    refund_bill_id: int
    expense_bill_id: int
    amount: float
    status: str
    idempotency_key: str
    request_payload: str
    created_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class RefundCommandBillVO:
    id: int
    amount: float
    aggregate_excluded: bool


@dataclass(frozen=True, slots=True)
class RefundAllocationAuditVO:
    id: int
    allocation_id: int
    action: str
    actor: str
    reason: str
    before_state: str
    after_state: str
    reverses_audit_id: int | None
    idempotency_key: str | None
    request_payload: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RefundNatureCommandAuditVO:
    id: int
    bill_id: int
    after_nature: str
    idempotency_key: str | None
    request_payload: str


@dataclass(frozen=True, slots=True)
class RefundNatureAuditVO:
    id: int
    bill_id: int
    action: str
    reason: str
    actor: str
    before_nature: str
    after_nature: str
    idempotency_key: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RefundBillSummaryVO:
    id: int
    occurred_at: datetime
    merchant: str
    amount: float
    currency: str
    account_name: str
