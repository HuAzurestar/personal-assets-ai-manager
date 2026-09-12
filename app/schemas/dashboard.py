from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.schemas.ledger import LedgerBillVO


@dataclass(frozen=True, slots=True)
class DashboardBillVO:
    id: int
    occurred_at: datetime
    merchant: str
    amount: float


@dataclass(frozen=True, slots=True)
class DashboardAllocationVO:
    id: int
    refund_bill_id: int
    expense_bill_id: int
    amount: float


@dataclass(frozen=True, slots=True)
class DashboardMatterVO:
    id: int
    lines: tuple[dict, ...]
    balances: tuple[dict, ...]


@dataclass(frozen=True, slots=True)
class DashboardCandidateVO:
    id: int
    status: str
    member_bill_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class DashboardDataVO:
    bills: tuple[DashboardBillVO, ...]
    allocations: tuple[DashboardAllocationVO, ...]
    refund_bill_ids: frozenset[int]
    matters: tuple[DashboardMatterVO, ...]
    warning_candidates: tuple[DashboardCandidateVO, ...]
    cash_amounts: tuple[float, ...]
    issue_count: int
    import_count: int
    candidate_count: int
    transfer_group_count: int


@dataclass(frozen=True, slots=True)
class ExcludedBillVO:
    id: int
    duplicate_of_id: int | None


@dataclass(frozen=True, slots=True)
class DrilldownEvidenceVO:
    bills: dict[int, LedgerBillVO]
    excluded: tuple[ExcludedBillVO, ...]
    sources: dict[int, dict]
    candidate_ids_by_bill: dict[int, tuple[int, ...]]
    candidate_actions: dict[int, tuple[dict, ...]]
    tag_audits: dict[int, tuple[dict, ...]]
    account_revisions: dict[int, tuple[dict, ...]]
    refund_allocations: dict[int, tuple[dict, ...]]
    refund_nature_audits: dict[int, tuple[dict, ...]]
    review_matters: dict[int, dict]
