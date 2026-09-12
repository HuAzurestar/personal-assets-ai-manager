from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class LedgerPageQuery:
    page: int = 1
    page_size: int = 50
    sort_by: str = "occurred_at"
    sort_order: str = "desc"
    date_from: date | None = None
    date_to: date | None = None
    amount_min: float | None = None
    amount_max: float | None = None
    source: tuple[str, ...] = ()
    account: tuple[str, ...] = ()
    direction: str | None = None
    q: str | None = None
    tag: tuple[str, ...] = ()
    scope: str = "effective"


@dataclass(frozen=True, slots=True)
class LedgerTagVO:
    view_id: int
    view_name: str
    view_system_name: str
    tag_id: int
    tag_name: str
    tag_system_name: str


@dataclass(frozen=True, slots=True)
class LedgerBillVO:
    id: int
    occurred_at: datetime
    merchant: str
    note: str
    amount: float
    currency: str
    category: str
    account_name: str
    aggregate_excluded: bool
    transfer_group_id: str | None
    duplicate_of_id: int | None
    tag_state: dict[str, str]
    source_type: str | None = None
    source_reference: str | None = None
    import_batch_id: int | None = None
    tag_revision_id: int = 0
    tags: tuple[LedgerTagVO, ...] = ()


@dataclass(frozen=True, slots=True)
class LedgerPageVO:
    items: tuple[LedgerBillVO, ...]
    total: int
    page: int
    page_size: int
    filters: dict[str, object] = field(default_factory=dict)
    sort: dict[str, str] = field(default_factory=dict)
