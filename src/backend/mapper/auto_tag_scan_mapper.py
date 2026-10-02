"""Set-oriented scan reads and short atomic commits for auto-tag work."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from backend.entity import (
    AutoTagRule,
    LedgerEntry,
    LedgerEntryTag,
    ReviewAllocation,
    ReviewCase,
    TagAssignmentRequest,
    TargetTag,
    TargetTagView,
    TransactionFact,
)
from backend.entity.auto_tag_rule import MAX_COUNTER_VALUE
from backend.mapper.auto_tag_rule_mapper import decode_method_config
from backend.mapper.setting_mapper import SettingMapper

ScanCommitStatus = Literal["COMMITTED", "STALE"]


@dataclass(frozen=True, slots=True)
class ScanToken:
    rule_id: int
    rule_revision: int
    scan_epoch: int
    scan_after_ledger_id: int


@dataclass(frozen=True, slots=True)
class ScanTarget:
    tag_id: int
    name: str


@dataclass(frozen=True, slots=True)
class ScanPage:
    token: ScanToken
    enabled: bool
    view_active: bool
    model_enabled: bool
    view_id: int
    model_id: int
    prompt: str
    amount_mode: int
    ledger_ids: tuple[int, ...]
    active_ledger_ids: frozenset[int]
    active_tag_states: dict[int, tuple[str, ...]]
    existing_request_ids: frozenset[int]
    targets: tuple[ScanTarget, ...]
    protected_sources: dict[int, "ProtectedScanSource"] = field(default_factory=dict)
    invalid_ledger_ids: frozenset[int] = frozenset()


@dataclass(frozen=True, slots=True)
class ScanCommitResult:
    status: ScanCommitStatus
    reason: str
    request_count: int = 0


@dataclass(frozen=True, slots=True)
class ProtectedScanSource:
    direction: Literal["IN", "OUT"]
    amount: int
    currency_code: str
    merchant: str
    summary: str
    occurred_time: datetime | None = None
    payment_channel: str = ""
    economic_type: str | None = None
    synthetic_allowed: bool = False


class AutoTagScanMapper:
    """Read one candidate page; writes belong to AutoTagScanBatchMapper."""

    def __init__(self, db: Session):
        self.db = db

    def is_synthetic_acceptance_database(self) -> bool:
        """Reject any database containing a non-fixture fact before scanning."""
        return self.db.scalar(select(TransactionFact.id).where(
            ~TransactionFact.fact_key.like("pirc24-gate-fictional-%"),
        ).limit(1)) is None

    def read_page(self, rule_id: int, *, limit: int) -> ScanPage | None:
        if limit < 1 or limit > 100:
            raise ValueError("scan page limit must be between 1 and 100")
        rule = self.db.execute(select(
            AutoTagRule.id,
            AutoTagRule.view_id,
            AutoTagRule.method_config_json,
            AutoTagRule.enabled,
            AutoTagRule.amount_mode,
            AutoTagRule.rule_revision,
            AutoTagRule.scan_after_ledger_id,
            AutoTagRule.scan_epoch,
        ).where(AutoTagRule.id == rule_id)).mappings().one_or_none()
        if rule is None:
            return None
        config = decode_method_config(rule["method_config_json"])
        model_id = int(config["model_id"])
        view_active = self.db.scalar(select(TargetTagView.status).where(
            TargetTagView.id == rule["view_id"],
        )) == "ACTIVE"
        setting = SettingMapper(self.db).get()
        automation = (
            setting["value"].get("automation", {})
            if setting is not None
            else {}
        )
        models = automation.get("models", []) if isinstance(automation, dict) else []
        model_enabled = any(
            isinstance(model, dict)
            and model.get("id") == model_id
            and model.get("enabled") is True
            for model in models
        )
        token = ScanToken(
            rule_id=rule["id"],
            rule_revision=rule["rule_revision"],
            scan_epoch=rule["scan_epoch"],
            scan_after_ledger_id=rule["scan_after_ledger_id"],
        )
        ledger_ids = tuple(self.db.scalars(select(
            LedgerEntry.id,
        ).where(
            LedgerEntry.id > token.scan_after_ledger_id,
        ).order_by(LedgerEntry.id).limit(limit)).all())
        if not ledger_ids:
            return ScanPage(
                token=token,
                enabled=bool(rule["enabled"]),
                view_active=view_active,
                model_enabled=model_enabled,
                view_id=rule["view_id"],
                model_id=model_id,
                prompt=str(config["prompt"]),
                amount_mode=rule["amount_mode"],
                ledger_ids=(),
                active_ledger_ids=frozenset(),
                active_tag_states={},
                existing_request_ids=frozenset(),
                targets=self._targets(rule["view_id"]),
            )

        active_ledger_ids = frozenset(self.db.scalars(select(
            distinct(ReviewAllocation.ledger_entry_id),
        ).join(
            ReviewCase,
            ReviewCase.id == ReviewAllocation.review_case_id,
        ).join(
            LedgerEntry, LedgerEntry.id == ReviewAllocation.ledger_entry_id,
        ).where(
            ReviewAllocation.ledger_entry_id.in_(ledger_ids),
            ReviewCase.status == 0,
            LedgerEntry.entry_type != 3,
        )).all())
        tag_rows = self.db.execute(select(
            LedgerEntryTag.ledger_id,
            TargetTag.system_name,
        ).join(
            TargetTag,
            TargetTag.id == LedgerEntryTag.tag_id,
        ).where(
            LedgerEntryTag.ledger_id.in_(ledger_ids),
            TargetTag.view_id == rule["view_id"],
            TargetTag.status == "ACTIVE",
        ).order_by(
            LedgerEntryTag.ledger_id,
            TargetTag.id,
        )).all()
        active_tag_states: dict[int, list[str]] = {}
        for ledger_id, system_name in tag_rows:
            active_tag_states.setdefault(ledger_id, []).append(system_name)
        existing_request_ids = frozenset(self.db.scalars(select(
            distinct(TagAssignmentRequest.ledger_id),
        ).where(
            TagAssignmentRequest.rule_id == rule_id,
            TagAssignmentRequest.rule_revision == token.rule_revision,
            TagAssignmentRequest.ledger_id.in_(ledger_ids),
        )).all())
        return ScanPage(
            token=token,
            enabled=bool(rule["enabled"]),
            view_active=view_active,
            model_enabled=model_enabled,
            view_id=rule["view_id"],
            model_id=model_id,
            prompt=str(config["prompt"]),
            amount_mode=rule["amount_mode"],
            ledger_ids=ledger_ids,
            active_ledger_ids=active_ledger_ids,
            active_tag_states={
                ledger_id: tuple(values)
                for ledger_id, values in active_tag_states.items()
            },
            existing_request_ids=existing_request_ids,
            targets=self._targets(rule["view_id"]),
        )

    def read_protected_source(
        self, ledger_id: int, *, synthetic_only: bool = False,
    ) -> ProtectedScanSource | None:
        row = self.db.execute(select(
            LedgerEntry.entry_direction,
            LedgerEntry.amount,
            LedgerEntry.currency_code,
            LedgerEntry.occurred_time,
            TransactionFact.counterparty_name,
            TransactionFact.summary,
        ).select_from(ReviewAllocation).join(
            LedgerEntry,
            LedgerEntry.id == ReviewAllocation.ledger_entry_id,
        ).join(
            ReviewCase,
            ReviewCase.id == ReviewAllocation.review_case_id,
        ).join(
            TransactionFact,
            TransactionFact.id == ReviewAllocation.transaction_fact_id,
        ).where(
            ReviewAllocation.ledger_entry_id == ledger_id,
            ReviewCase.status == 0,
            LedgerEntry.entry_type != 3,
            *(
                (TransactionFact.fact_key.like("pirc24-gate-fictional-%"),)
                if synthetic_only else ()
            ),
        ).limit(1)).mappings().one_or_none()
        if row is None:
            return None
        return ProtectedScanSource(
            direction="IN" if row["entry_direction"] == 1 else "OUT",
            amount=int(row["amount"]),
            currency_code=str(row["currency_code"]),
            merchant=str(row["counterparty_name"]),
            summary=str(row["summary"]),
            occurred_time=row["occurred_time"],
        )

    def _targets(self, view_id: int) -> tuple[ScanTarget, ...]:
        rows = self.db.execute(select(
            TargetTag.id,
            TargetTag.name,
        ).where(
            TargetTag.view_id == view_id,
            TargetTag.status == "ACTIVE",
            TargetTag.system_name != "unclassified",
        ).order_by(TargetTag.id)).all()
        return tuple(ScanTarget(tag_id=tag_id, name=name) for tag_id, name in rows)

    @staticmethod
    def _counter(current: int, delta: int, name: str) -> int:
        if current > MAX_COUNTER_VALUE - delta:
            raise OverflowError(f"automatic tag rule {name} is exhausted")
        return current + delta
