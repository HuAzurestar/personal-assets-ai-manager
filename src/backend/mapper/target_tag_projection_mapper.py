from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import Text, and_, bindparam, delete, insert, select
from sqlalchemy.orm import Session

from backend.entity import (
    LedgerEntry,
    LedgerEntryTag,
    ReviewAllocation,
    ReviewCase,
    TargetTag,
    TargetTagView,
    TransactionFact,
)
from backend.entity.base import utc_now
from backend.error import TargetEconomicError


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
        ).outerjoin(
            TargetTag,
            and_(TargetTag.view_id == TargetTagView.id, TargetTag.status == "ACTIVE"),
        ).where(
            TargetTagView.status == "ACTIVE",
        ).order_by(TargetTagView.id, TargetTag.id)).mappings().all()
        defaults = {row["view_id"] for row in rows if row["tag_system_name"] == "unclassified"}
        if any(row["view_id"] not in defaults for row in rows):
            raise TargetEconomicError(409, "active View requires an active default Tag", code="TAG_RELATION_BROKEN")
        return tuple(ActiveTagValue(**row) for row in rows)

    def validate_ledgers(self, ledger_ids):
        rows = self.db.execute(select(LedgerEntry.id, ReviewAllocation.id.label("allocation_id"),
            ReviewCase.id.label("review_id"), TransactionFact.id.label("fact_id")
        ).outerjoin(ReviewAllocation, ReviewAllocation.ledger_entry_id == LedgerEntry.id
        ).outerjoin(ReviewCase, ReviewCase.id == ReviewAllocation.review_case_id
        ).outerjoin(TransactionFact, TransactionFact.id == ReviewAllocation.transaction_fact_id
        ).where(LedgerEntry.id.in_(ledger_ids))).mappings().all()
        seen = set()
        for row in rows:
            if row["id"] in seen or any(row[key] is None for key in ("allocation_id", "review_id", "fact_id")):
                raise TargetEconomicError(409, "Ledger tag source relation is damaged", code="TAG_RELATION_BROKEN")
            seen.add(row["id"])
        if seen != set(ledger_ids):
            raise TargetEconomicError(409, "Ledger tag source relation is missing", code="TAG_RELATION_BROKEN")

    def active_ledger_ids(self) -> list[int]:
        return list(self.db.scalars(select(LedgerEntry.id).join(
            ReviewAllocation,
            ReviewAllocation.ledger_entry_id == LedgerEntry.id,
        ).join(
            ReviewCase,
            ReviewCase.id == ReviewAllocation.review_case_id,
        ).where(
            ReviewCase.status == 0,
        ).distinct().order_by(LedgerEntry.id)).all())

    def current_states(
        self,
        ledger_ids: list[int],
    ) -> dict[int, dict[str, str]]:
        if not ledger_ids:
            return {}
        self.validate_ledgers(ledger_ids)
        rows = self.db.execute(select(
            LedgerEntryTag.ledger_id,
            TargetTagView.system_name.label("view_system_name"),
            TargetTag.system_name.label("tag_system_name"),
            TargetTagView.status.label("view_status"), TargetTag.status.label("tag_status"),
        ).outerjoin(
            TargetTag,
            TargetTag.id == LedgerEntryTag.tag_id,
        ).outerjoin(
            TargetTagView,
            TargetTagView.id == TargetTag.view_id,
        ).where(
            LedgerEntryTag.ledger_id.in_(ledger_ids),
        ).order_by(
            LedgerEntryTag.ledger_id,
            TargetTagView.id,
            TargetTag.id,
        )).mappings().all()
        states: dict[int, dict[str, str]] = {}
        for row in rows:
            if row["view_system_name"] is None or row["tag_system_name"] is None:
                raise TargetEconomicError(409, "Tag or View relation is missing", code="TAG_RELATION_BROKEN")
            if row["view_status"] != "ACTIVE" or row["tag_status"] != "ACTIVE":
                continue
            state = states.setdefault(row["ledger_id"], {})
            view_name = row["view_system_name"]
            if view_name in state:
                raise TargetEconomicError(409, "multiple active Tags in one View", code="TAG_RELATION_BROKEN")
            state[view_name] = row["tag_system_name"]
        return states

    def replace(self, ledger_tags: dict[int, tuple[int, ...]]) -> None:
        ledger_ids = list(ledger_tags)
        if not ledger_ids:
            return
        rows = self.db.execute(select(
            LedgerEntryTag.ledger_id,
            LedgerEntryTag.tag_id,
            LedgerEntryTag.updated_time,
        ).where(
            LedgerEntryTag.ledger_id.in_(ledger_ids)
        ).order_by(
            LedgerEntryTag.ledger_id,
            LedgerEntryTag.tag_id,
        )).all()
        current = {ledger_id: [] for ledger_id in ledger_ids}
        previous_updated_times: dict[int, datetime | None] = {
            ledger_id: None for ledger_id in ledger_ids
        }
        for ledger_id, tag_id, updated_time in rows:
            current[ledger_id].append(tag_id)
            previous = previous_updated_times[ledger_id]
            if previous is None or updated_time > previous:
                previous_updated_times[ledger_id] = updated_time
        changed = [
            ledger_id
            for ledger_id, tag_ids in ledger_tags.items()
            if tuple(current[ledger_id]) != tuple(sorted(tag_ids))
        ]
        if not changed:
            return
        changed_ids = set(changed)
        self.db.execute(delete(LedgerEntryTag).where(LedgerEntryTag.ledger_id.in_(changed)))
        current_time = utc_now()
        updated_times = {
            ledger_id: max(
                current_time,
                previous_updated_times[ledger_id] + timedelta(microseconds=1),
            ) if previous_updated_times[ledger_id] is not None else current_time
            for ledger_id in changed
        }
        table = LedgerEntryTag.__table__
        dialect = self.db.get_bind().dialect
        # All Tags of one Ledger share its exact replacement timestamp. Run
        # the existing column codecs once per Ledger rather than twice per Tag.
        # Explicit text binds retain that validated six-digit fractional UTC text;
        # no clock truncation, alternate codec or persistent cache is involved.
        stored_times = {ledger_id: dict(
            tag_created_time=table.c.created_time.type.process_bind_param(value, dialect),
            tag_updated_time=table.c.updated_time.type.process_bind_param(value, dialect),
        ) for ledger_id, value in updated_times.items()}
        records = [dict(ledger_id=ledger_id, tag_id=tag_id, **stored_times[ledger_id])
            for ledger_id, tag_ids in ledger_tags.items()
            if ledger_id in changed_ids
            for tag_id in tag_ids
        ]
        statement = insert(table).values(
            created_time=bindparam("tag_created_time", type_=Text()),
            updated_time=bindparam("tag_updated_time", type_=Text()),
        )
        for offset in range(0, len(records), 400):
            self.db.execute(statement, records[offset:offset + 400])
        self.db.flush()
