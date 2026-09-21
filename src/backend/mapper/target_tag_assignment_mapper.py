from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from backend.entity import (
    LedgerEntry,
    LedgerEntryTag,
    TargetTag,
    TargetTagView,
)


class TargetTagAssignmentMapper:
    """Direct LedgerEntry-to-Tag persistence."""

    def __init__(self, db: Session):
        self.db = db

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    def ledger(self, ledger_id: int) -> dict | None:
        row = self.db.execute(select(
            LedgerEntry.id,
            LedgerEntry.updated_time,
        ).where(
            LedgerEntry.id == ledger_id,
        )).mappings().one_or_none()
        return dict(row) if row is not None else None

    def tag_ids(self, state: dict[str, str]) -> list[int]:
        rows = self.db.execute(select(
            TargetTagView.system_name.label("view_name"),
            TargetTag.system_name.label("tag_name"),
            TargetTag.id,
        ).join(TargetTag, TargetTag.view_id == TargetTagView.id).where(
            TargetTagView.status == "ACTIVE",
            TargetTag.status == "ACTIVE",
        )).mappings().all()
        mapping = {(row["view_name"], row["tag_name"]): row["id"] for row in rows}
        return [mapping[(view, tag)] for view, tag in sorted(state.items())]

    def replace(self, ledger_id: int, tag_ids: list[int]) -> None:
        self.db.execute(delete(LedgerEntryTag).where(
            LedgerEntryTag.ledger_id == ledger_id
        ))
        self.db.add_all([
            LedgerEntryTag(ledger_id=ledger_id, tag_id=tag_id)
            for tag_id in tag_ids
        ])

    def current_tag_ids(self, ledger_id: int) -> tuple[int, ...]:
        return tuple(self.db.scalars(select(LedgerEntryTag.tag_id).where(
            LedgerEntryTag.ledger_id == ledger_id
        ).order_by(LedgerEntryTag.tag_id)).all())

    def touch(
        self,
        ledger_id: int,
        expected_updated_time: datetime,
        now: datetime,
    ) -> bool:
        result = self.db.execute(update(LedgerEntry).where(
            LedgerEntry.id == ledger_id,
            LedgerEntry.updated_time == expected_updated_time,
        ).values(updated_time=now))
        return result.rowcount == 1

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()
