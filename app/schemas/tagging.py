from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel


class TagStateBulkResult(BaseModel):
    updated: int
    bill_ids: list[int]


@dataclass(frozen=True, slots=True)
class TagCommandBillVO:
    id: int
    category: str
    tag_state_json: str


@dataclass(frozen=True, slots=True)
class TagViewVO:
    id: int
    name: str
    system_name: str


@dataclass(frozen=True, slots=True)
class TagValueVO:
    id: int
    view_id: int
    name: str
    system_name: str
    is_unclassified: bool


@dataclass(frozen=True, slots=True)
class TagAuditVO:
    id: int
    bill_id: int
    idempotency_key: str | None
    request_payload: str
    undone: bool


@dataclass(frozen=True, slots=True)
class TagAuditWriteVO:
    bill_id: int
    category: str
    tags: str
    tag_state_json: str
    strategy: str
    confidence: float
    provider: str
    superseded: bool
    action: str
    actor: str
    reason: str
    before_state_json: str
    before_category: str
    idempotency_key: str | None
    request_payload: str
    created_at: datetime
