from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.entity import (
    LedgerEntry,
    LedgerEntryTag,
    TargetTag,
    TargetTagView,
)


@dataclass(frozen=True, slots=True)
class ActiveTagValue:
    view_id: int
    view_system_name: str
    tag_id: int
    tag_system_name: str


class TargetTagProjectionMapper:
    """Set-oriented reads and writes for direct Ledger Tag state."""

    def __init__(self, db: Session):
        self.db = db

    def active_dictionary(self) -> tuple[ActiveTagValue, ...]:
        rows = self.db.execute(select(
            TargetTagView.id.label("view_id"),
            TargetTagView.system_name.label("view_system_name"),
            TargetTag.id.label("tag_id"),
            TargetTag.system_name.label("tag_system_name"),
        ).join(
            TargetTag,
            TargetTag.view_id == TargetTagView.id,
        ).where(
            TargetTagView.status == "ACTIVE",
            TargetTag.status == "ACTIVE",
        ).order_by(TargetTagView.id, TargetTag.id)).mappings().all()
        return tuple(ActiveTagValue(**row) for row in rows)

    def active_ledger_ids(self) -> list[int]:
        return list(self.db.scalars(select(LedgerEntry.id).where(
            LedgerEntry.status == "ACTIVE",
        ).order_by(LedgerEntry.id)).all())

    def current_states(
        self,
        ledger_ids: list[int],
    ) -> dict[int, dict[str, str]]:
        if not ledger_ids:
            return {}
        rows = self.db.execute(select(
            LedgerEntryTag.ledger_id,
            TargetTagView.system_name.label("view_system_name"),
            TargetTag.system_name.label("tag_system_name"),
        ).join(
            TargetTag,
            TargetTag.id == LedgerEntryTag.tag_id,
        ).join(
            TargetTagView,
            TargetTagView.id == TargetTag.view_id,
        ).where(
            LedgerEntryTag.ledger_id.in_(ledger_ids),
            TargetTagView.status == "ACTIVE",
            TargetTag.status == "ACTIVE",
        ).order_by(
            LedgerEntryTag.ledger_id,
            TargetTagView.id,
            TargetTag.id,
        )).mappings().all()
        states: dict[int, dict[str, str]] = {}
        for row in rows:
            state = states.setdefault(row["ledger_id"], {})
            view_name = row["view_system_name"]
            if view_name in state:
                raise ValueError(
                    f"ledger {row['ledger_id']} has multiple active tags in "
                    f"view {view_name}"
                )
            state[view_name] = row["tag_system_name"]
        return states

    def replace(self, ledger_tags: dict[int, tuple[int, ...]]) -> None:
        ledger_ids = list(ledger_tags)
        if not ledger_ids:
            return
        self.db.execute(delete(LedgerEntryTag).where(
            LedgerEntryTag.ledger_id.in_(ledger_ids)
        ))
        self.db.add_all([
            LedgerEntryTag(ledger_id=ledger_id, tag_id=tag_id)
            for ledger_id, tag_ids in ledger_tags.items()
            for tag_id in tag_ids
        ])
        self.db.flush()
