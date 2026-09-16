from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from backend.entity import LedgerEntry, LedgerEntryTag, TargetTag, TargetTagView


@dataclass(frozen=True, slots=True)
class TagAssignmentTarget:
    ledger_id: int
    projection_version: int


class TargetTagAssignmentMapper:
    """Bounded SQL for one direct Ledger Tag replacement."""

    def __init__(self, db: Session):
        self.db = db

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    def target(self, ledger_id: int) -> TagAssignmentTarget | None:
        row = self.db.execute(select(
            LedgerEntry.id.label("ledger_id"),
            LedgerEntry.projection_version,
        ).where(LedgerEntry.id == ledger_id)).mappings().one_or_none()
        return TagAssignmentTarget(**row) if row else None

    def state(self, ledger_id: int) -> dict[str, str]:
        rows = self.db.execute(select(
            TargetTagView.system_name.label("view_system_name"),
            TargetTag.system_name.label("tag_system_name"),
        ).select_from(LedgerEntryTag).join(
            TargetTag,
            TargetTag.id == LedgerEntryTag.tag_id,
        ).join(
            TargetTagView,
            TargetTagView.id == TargetTag.view_id,
        ).where(
            LedgerEntryTag.ledger_id == ledger_id,
            TargetTagView.status == "ACTIVE",
            TargetTag.status == "ACTIVE",
        ).order_by(TargetTagView.id, TargetTag.id)).mappings().all()
        state: dict[str, str] = {}
        for row in rows:
            view_name = row["view_system_name"]
            if view_name in state:
                raise ValueError(
                    f"ledger {ledger_id} has multiple active tags in view {view_name}"
                )
            state[view_name] = row["tag_system_name"]
        return state

    def replace(self, ledger_id: int, tag_ids: tuple[int, ...]) -> None:
        self.db.execute(delete(LedgerEntryTag).where(
            LedgerEntryTag.ledger_id == ledger_id,
        ))
        self.db.add_all([
            LedgerEntryTag(ledger_id=ledger_id, tag_id=tag_id)
            for tag_id in tag_ids
        ])
        self.db.flush()

    def advance_projection(
        self,
        ledger_id: int,
        expected_version: int,
        now: datetime,
    ) -> int:
        result = self.db.execute(update(LedgerEntry).where(
            LedgerEntry.id == ledger_id,
            LedgerEntry.projection_version == expected_version,
        ).values(
            projection_version=expected_version + 1,
            updated_time=now,
        ))
        if result.rowcount != 1:
            raise ValueError("ledger projection changed; reload before assigning tags")
        return expected_version + 1

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()
