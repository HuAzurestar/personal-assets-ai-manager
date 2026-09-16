from __future__ import annotations

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from backend.entity import (
    LedgerEntry,
    LedgerEntryTag,
    ReviewAllocation,
    ReviewCase,
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

    def active_ledger_exists(self, ledger_id: int) -> bool:
        return self.db.scalar(select(LedgerEntry.id).join(
            ReviewAllocation,
            ReviewAllocation.ledger_entry_id == LedgerEntry.id,
        ).join(
            ReviewCase,
            ReviewCase.id == ReviewAllocation.review_case_id,
        ).where(
            LedgerEntry.id == ledger_id,
            ReviewCase.status == 0,
        )) is not None

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

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()
