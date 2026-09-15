from __future__ import annotations

from dataclasses import dataclass
import json

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.entity import (
    LedgerEntrySource,
    LedgerEntryTag,
    ReviewCase,
    ReviewCaseBill,
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
    """Set-oriented reads and writes for effective hot-ledger tags."""

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

    def confirmed_states(self, fact_ids: list[int]) -> dict[int, dict[str, str]]:
        if not fact_ids:
            return {}
        rows = self.db.execute(select(
            ReviewCaseBill.bill_id,
            ReviewCase.id.label("case_id"),
            ReviewCase.result_json,
        ).join(
            ReviewCase,
            ReviewCase.id == ReviewCaseBill.case_id,
        ).where(
            ReviewCaseBill.bill_id.in_(fact_ids),
            ReviewCase.review_type == "TAG",
            ReviewCase.status == "CONFIRMED",
        ).order_by(ReviewCaseBill.bill_id, ReviewCase.id)).mappings().all()
        states: dict[int, dict[str, str]] = {}
        owners: dict[int, int] = {}
        for row in rows:
            fact_id = row["bill_id"]
            if fact_id in states:
                raise ValueError(
                    f"fact {fact_id} has multiple confirmed TAG reviews: "
                    f"{owners[fact_id]}, {row['case_id']}"
                )
            try:
                payload = json.loads(row["result_json"])
                state = payload["tag_state"]
            except (json.JSONDecodeError, TypeError, KeyError):
                raise ValueError(f"TAG review {row['case_id']} has invalid tag_state") from None
            if not isinstance(state, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in state.items()
            ):
                raise ValueError(f"TAG review {row['case_id']} has invalid tag_state")
            states[fact_id] = state
            owners[fact_id] = row["case_id"]
        return states

    def all_fact_ledgers(self) -> dict[int, int]:
        rows = self.db.execute(select(
            LedgerEntrySource.source_id,
            LedgerEntrySource.ledger_id,
        ).where(
            LedgerEntrySource.source_kind == "BILL_FACT",
        )).mappings().all()
        return {row["source_id"]: row["ledger_id"] for row in rows}

    def replace(self, ledger_tags: dict[int, tuple[int, ...]]) -> None:
        ledger_ids = list(ledger_tags)
        if not ledger_ids:
            return
        self.db.execute(delete(LedgerEntryTag).where(
            LedgerEntryTag.ledger_id.in_(ledger_ids)
        ))
        now_rows = [
            LedgerEntryTag(ledger_id=ledger_id, tag_id=tag_id)
            for ledger_id, tag_ids in ledger_tags.items()
            for tag_id in tag_ids
        ]
        self.db.add_all(now_rows)
        self.db.flush()
