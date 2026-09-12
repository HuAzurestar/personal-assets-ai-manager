from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel


class ImportIssueActionRead(BaseModel):
    action: str
    payload: dict[str, object]
    actor: str
    created_at: datetime


class ImportIssueRead(BaseModel):
    id: int
    filename: str
    batch_id: int
    row_number: int
    raw_fields: dict[str, object]
    error: str
    bill_id: int | None
    resolved_at: datetime | None
    resolution: str
    history: list[ImportIssueActionRead]


class ImportIssueSummaryRead(BaseModel):
    id: int
    filename: str
    batch_id: int
    row_number: int
    error: str
    bill_id: int | None
    resolved_at: datetime | None
    status: str


class ImportIssuePageRead(BaseModel):
    items: list[ImportIssueSummaryRead]
    total: int
    page: int
    page_size: int


class ImportIssueStatusRead(BaseModel):
    id: int
    status: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ImportIssueVO:
    id: int
    filename: str | None
    batch_id: int
    source_row_number: int
    raw_payload: str
    error: str
    bill_id: int | None
    resolved_at: datetime | None
    resolution: str


@dataclass(frozen=True, slots=True)
class ImportIssueActionVO:
    id: int
    issue_id: int
    action: str
    payload: str
    actor: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ImportIssueSummaryVO:
    id: int
    filename: str | None
    batch_id: int
    source_row_number: int
    error: str
    bill_id: int | None
    resolved_at: datetime | None


@dataclass(frozen=True, slots=True)
class ImportIssueCommandVO:
    id: int
    batch_id: int
    source_type: str | None
    source_row_number: int
    raw_payload: str
    bill_id: int | None
    resolved_at: datetime | None
