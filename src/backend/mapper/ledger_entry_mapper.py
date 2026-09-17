from __future__ import annotations

import json
from datetime import datetime, time

from sqlalchemy import case, exists, func, literal, or_, select
from sqlalchemy.orm import Session

from backend.entity import (
    CASH_DIRECTION_IN,
    CASH_DIRECTION_OUT,
    LedgerEntry,
    LedgerEntryTag,
    ReviewAllocation,
    ReviewCase,
    ReviewRevision,
    TargetTag,
    TargetTagView,
    TransactionFact,
)
from backend.schema.target_economic import EconomicPageQuery, EconomicSummaryQuery


class LedgerEntryMapper:
    """Explicit-column reads for confirmed Ledger entries."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _flow_columns():
        return (
            LedgerEntry.id,
            LedgerEntry.entry_type,
            LedgerEntry.entry_direction,
            LedgerEntry.amount,
            LedgerEntry.currency_code,
            LedgerEntry.account_code,
            LedgerEntry.counterparty_account_ref,
            LedgerEntry.occurred_time,
            LedgerEntry.updated_time,
        )

    def page(self, query: EconomicPageQuery):
        clauses = self._clauses(query)
        total = self.db.scalar(select(func.count(LedgerEntry.id)).where(*clauses)) or 0
        sort_columns = {
            "id": LedgerEntry.id,
            "occurred_time": LedgerEntry.occurred_time,
            "amount": LedgerEntry.amount,
            "projection_version": LedgerEntry.updated_time,
        }
        column = sort_columns[query.sort_field]
        order = column.asc() if query.sort_order == "asc" else column.desc()
        id_order = LedgerEntry.id.asc() if query.sort_order == "asc" else LedgerEntry.id.desc()
        rows = self.db.execute(select(*self._flow_columns()).where(*clauses).order_by(
            order,
            id_order,
        ).offset((query.page - 1) * query.page_size).limit(query.page_size)).mappings().all()
        return rows, total

    def detail(self, ledger_id: int):
        flow = self.db.execute(select(*self._flow_columns()).where(
            LedgerEntry.id == ledger_id,
            *self._active_clauses(),
        )).mappings().one_or_none()
        if flow is None:
            return None
        allocations = self.db.execute(select(
            ReviewAllocation.id,
            ReviewAllocation.review_case_id.label("review_id"),
            ReviewAllocation.transaction_fact_id.label("fact_id"),
            ReviewAllocation.ledger_entry_id.label("economic_id"),
            ReviewAllocation.amount,
            ReviewAllocation.currency_code,
        ).join(
            ReviewCase,
            ReviewCase.id == ReviewAllocation.review_case_id,
        ).where(
            ReviewAllocation.ledger_entry_id == ledger_id,
            ReviewCase.status == 0,
        ).order_by(ReviewAllocation.id)).mappings().all()
        fact_ids = sorted({row["fact_id"] for row in allocations})
        facts = self.db.execute(select(
            TransactionFact.id,
            TransactionFact.occurred_time,
            TransactionFact.cash_direction,
            TransactionFact.amount,
            TransactionFact.currency_code,
            TransactionFact.account_code,
            TransactionFact.counterparty_name.label("counterparty"),
            TransactionFact.summary,
        ).where(
            TransactionFact.id.in_(fact_ids)
        ).order_by(TransactionFact.id)).mappings().all() if fact_ids else []
        facts = [self._fact_values(row) for row in facts]
        review_ids = sorted({row["review_id"] for row in allocations})
        reviews = self.db.execute(select(
            ReviewCase.id,
            literal("ECONOMIC").label("review_type"),
            case(
                (ReviewCase.behavior_type == 0, "DEFAULT"),
                (ReviewCase.behavior_type == 1, "BORROW_AND_REPAY"),
                else_="UNKNOWN",
            ).label("behavior_code"),
            case(
                (ReviewCase.status == 0, "CONFIRMED"),
                (ReviewCase.status == 1, "REVOKED"),
                else_="UNKNOWN",
            ).label("status"),
            ReviewCase.title,
        ).where(
            ReviewCase.id.in_(review_ids),
        ).order_by(ReviewCase.id)).mappings().all() if review_ids else []
        review_versions = dict(self.db.execute(select(
            ReviewRevision.review_case_id,
            func.count(ReviewRevision.id),
        ).where(
            ReviewRevision.review_case_id.in_(review_ids),
        ).group_by(ReviewRevision.review_case_id)).all()) if review_ids else {}
        behavior_codes = {}
        if review_ids:
            revision_rows = self.db.execute(select(
                ReviewRevision.review_case_id,
                ReviewRevision.request_json,
            ).where(
                ReviewRevision.review_case_id.in_(review_ids),
            ).order_by(ReviewRevision.id.desc())).mappings().all()
            for revision in revision_rows:
                payload = json.loads(revision["request_json"] or "{}")
                if (
                    revision["review_case_id"] not in behavior_codes
                    and payload.get("behavior_code")
                ):
                    behavior_codes[revision["review_case_id"]] = payload["behavior_code"]
        reviews = [
            {
                **dict(row),
                "review_type": behavior_codes.get(row["id"], row["behavior_code"]),
                "behavior_code": behavior_codes.get(row["id"], row["behavior_code"]),
                "version": max(1, int(review_versions.get(row["id"], 0))),
            }
            for row in reviews
        ]
        return (
            flow,
            allocations,
            facts,
            reviews,
            self.tags([ledger_id]).get(ledger_id, []),
        )

    def tags(self, ledger_ids: list[int]) -> dict[int, list[dict]]:
        if not ledger_ids:
            return {}
        rows = self.db.execute(select(
            LedgerEntryTag.ledger_id,
            TargetTagView.name.label("view_name"),
            TargetTagView.system_name.label("view_system_name"),
            TargetTag.name.label("tag_name"),
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
        result: dict[int, list[dict]] = {}
        for row in rows:
            result.setdefault(row["ledger_id"], []).append({
                key: value for key, value in row.items() if key != "ledger_id"
            })
        return result

    def summary(self, query: EconomicSummaryQuery):
        clauses = self._active_clauses()
        if query.date_from:
            clauses.append(
                LedgerEntry.occurred_time >= datetime.combine(query.date_from, time.min)
            )
        if query.date_to:
            clauses.append(
                LedgerEntry.occurred_time <= datetime.combine(query.date_to, time.max)
            )
        return self.db.execute(select(
            LedgerEntry.id,
            LedgerEntry.entry_type,
            LedgerEntry.entry_direction,
            LedgerEntry.amount,
            LedgerEntry.currency_code,
        ).where(*clauses)).mappings().all()

    @staticmethod
    def _clauses(query: EconomicPageQuery) -> list:
        clauses = LedgerEntryMapper._active_clauses()
        if query.date_from:
            clauses.append(
                LedgerEntry.occurred_time >= datetime.combine(query.date_from, time.min)
            )
        if query.date_to:
            clauses.append(
                LedgerEntry.occurred_time <= datetime.combine(query.date_to, time.max)
            )
        if query.entry_type:
            clauses.append(LedgerEntry.entry_type.in_(query.entry_type))
        if query.currency_code:
            clauses.append(LedgerEntry.currency_code.in_(query.currency_code))
        if query.cash_direction:
            clauses.append(LedgerEntry.entry_direction == query.cash_direction)
        if query.account_code:
            clauses.append(LedgerEntry.account_code == query.account_code)
        if query.q:
            clauses.append(exists(select(ReviewAllocation.id).join(
                ReviewCase,
                ReviewCase.id == ReviewAllocation.review_case_id,
            ).join(
                TransactionFact,
                TransactionFact.id == ReviewAllocation.transaction_fact_id,
            ).where(
                ReviewAllocation.ledger_entry_id == LedgerEntry.id,
                ReviewCase.status == 0,
                or_(
                    ReviewCase.title.ilike(f"%{query.q}%"),
                    TransactionFact.counterparty_name.ilike(f"%{query.q}%"),
                    TransactionFact.summary.ilike(f"%{query.q}%"),
                ),
            )))
        return clauses

    @staticmethod
    def _active_clauses() -> list:
        return [
            LedgerEntry.entry_type.in_((0, 1, 2)),
            LedgerEntry.entry_direction.in_((1, 2)),
            LedgerEntry.amount > 0,
            LedgerEntry.occurred_time.is_not(None),
            exists(select(ReviewAllocation.id).join(
                ReviewCase,
                ReviewCase.id == ReviewAllocation.review_case_id,
            ).where(
                ReviewAllocation.ledger_entry_id == LedgerEntry.id,
                ReviewCase.status == 0,
            )),
        ]

    @staticmethod
    def _fact_values(row) -> dict:
        values = dict(row)
        direction = {
            CASH_DIRECTION_IN: "IN",
            CASH_DIRECTION_OUT: "OUT",
        }.get(values["cash_direction"])
        if direction is None:
            raise ValueError(
                f"unknown transaction_fact cash_direction: "
                f"{values['cash_direction']}"
            )
        values["cash_direction"] = direction
        return values
