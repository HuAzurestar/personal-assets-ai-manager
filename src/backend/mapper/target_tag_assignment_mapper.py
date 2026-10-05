from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from backend.entity import (
    LedgerEntry,
    LedgerEntryTag,
    ReviewAllocation,
    ReviewCase,
    TransactionFact,
    TargetTag,
    TargetTagView,
)
from backend.error import TargetTagError
from backend.mapper.tag_write_mapper import TagWriteMapper


class TargetTagAssignmentMapper(TagWriteMapper):
    """Direct LedgerEntry-to-Tag persistence."""

    def ledger_exists(self, ledger_id: int) -> bool:
        return self.ledger_status(ledger_id) is not None

    def ledger_status(self, ledger_id):
        rows = self.db.execute(select(ReviewCase.status, ReviewAllocation.id, TransactionFact.id
            ).select_from(LedgerEntry).outerjoin(ReviewAllocation, ReviewAllocation.ledger_entry_id == LedgerEntry.id
            ).outerjoin(ReviewCase, ReviewCase.id == ReviewAllocation.review_case_id
            ).outerjoin(TransactionFact, TransactionFact.id == ReviewAllocation.transaction_fact_id
            ).where(LedgerEntry.id == ledger_id)).all()
        if not rows:
            return None
        if len(rows) != 1 or any(value is None for value in rows[0]) or rows[0][0] not in (0, 1):
            raise TargetTagError(409, "Ledger source relation is damaged", code="TAG_RELATION_BROKEN")
        return rows[0][0]

    def assignment(self, ledger_id: int) -> dict[str, object]:
        rows = self.db.execute(select(
            LedgerEntryTag.tag_id,
            LedgerEntryTag.updated_time,
            TargetTagView.system_name.label("view_name"),
            TargetTagView.status.label("view_status"),
            TargetTag.system_name.label("tag_name"),
            TargetTag.status.label("tag_status"),
        ).outerjoin(
            TargetTag,
            TargetTag.id == LedgerEntryTag.tag_id,
        ).outerjoin(
            TargetTagView,
            TargetTagView.id == TargetTag.view_id,
        ).where(
            LedgerEntryTag.ledger_id == ledger_id,
        ).order_by(LedgerEntryTag.tag_id)).mappings().all()
        state: dict[str, str] = {}
        for row in rows:
            if row["view_name"] is None or row["tag_name"] is None:
                raise TargetTagError(409, "Tag or View relation is missing", code="TAG_RELATION_BROKEN")
            if row["view_status"] != "ACTIVE" or row["tag_status"] != "ACTIVE":
                continue
            if row["view_name"] in state:
                raise TargetTagError(409, "multiple active Tags in one View", code="TAG_RELATION_BROKEN")
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
        *,
        previous_tag_ids: tuple[int, ...],
        touched_tag_ids: set[int],
    ) -> None:
        self.db.execute(delete(LedgerEntryTag).where(
            LedgerEntryTag.ledger_id == ledger_id,
            LedgerEntryTag.tag_id.not_in(tag_ids),
        ))
        self.db.execute(update(LedgerEntryTag).where(
            LedgerEntryTag.ledger_id == ledger_id,
            LedgerEntryTag.tag_id.in_(touched_tag_ids),
        ).values(
            updated_time=now,
        ))
        self.db.add_all([
            LedgerEntryTag(
                ledger_id=ledger_id,
                tag_id=tag_id,
                created_time=now,
                updated_time=now,
            )
            for tag_id in tag_ids
            if tag_id not in previous_tag_ids
        ])
