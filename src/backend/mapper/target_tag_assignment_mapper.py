from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select, text
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

    def ledger_exists(self, ledger_id: int) -> bool:
        return self.db.scalar(select(LedgerEntry.id).where(
            LedgerEntry.id == ledger_id,
        )) is not None

    def assignment(self, ledger_id: int) -> dict[str, object]:
        rows = self.db.execute(select(
            LedgerEntryTag.tag_id,
            LedgerEntryTag.updated_time,
            TargetTagView.system_name.label("view_name"),
            TargetTagView.status.label("view_status"),
            TargetTag.system_name.label("tag_name"),
            TargetTag.status.label("tag_status"),
        ).join(
            TargetTag,
            TargetTag.id == LedgerEntryTag.tag_id,
        ).join(
            TargetTagView,
            TargetTagView.id == TargetTag.view_id,
        ).where(
            LedgerEntryTag.ledger_id == ledger_id,
        ).order_by(LedgerEntryTag.tag_id)).mappings().all()
        state: dict[str, str] = {}
        for row in rows:
            if row["view_status"] != "ACTIVE" or row["tag_status"] != "ACTIVE":
                continue
            if row["view_name"] in state:
                raise ValueError(
                    f"ledger {ledger_id} has multiple active tags in "
                    f"view {row['view_name']}"
                )
            state[row["view_name"]] = row["tag_name"]
        return {
            "tag_ids": tuple(row["tag_id"] for row in rows),
            "tag_state": state,
            "updated_time": max(
                (row["updated_time"] for row in rows),
                default=None,
            ),
        }

    def replace(
        self,
        ledger_id: int,
        tag_ids: list[int],
        now: datetime,
    ) -> None:
        self.db.execute(delete(LedgerEntryTag).where(
            LedgerEntryTag.ledger_id == ledger_id
        ))
        self.db.add_all([
            LedgerEntryTag(
                ledger_id=ledger_id,
                tag_id=tag_id,
                created_time=now,
                updated_time=now,
            )
            for tag_id in tag_ids
        ])

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()
