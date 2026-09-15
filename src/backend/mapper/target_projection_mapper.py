from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.entity import (
    BillFact,
    BillRaw,
    LedgerEntry,
    LedgerEntrySource,
    ReviewCase,
    ReviewCaseBill,
)
from backend.schema.target_projection import (
    DefaultProjectionFactVO,
    DefaultProjectionWriteVO,
    FactNatureEvidenceVO,
)


FINANCIAL_REVIEW_TYPES = {
    "AA",
    "LOAN_BORROW",
    "LOAN_LEND",
    "REFUND",
    "TRANSFER",
    "FX_EXCHANGE",
    "DUPLICATE",
}


class TargetProjectionMapper:
    """Bounded target projection reads/writes for affected fact IDs."""

    def __init__(self, db: Session):
        self.db = db

    def facts(self, fact_ids: list[int]) -> tuple[DefaultProjectionFactVO, ...]:
        if not fact_ids:
            return ()
        rows = self.db.execute(select(
            BillFact.id,
            BillFact.fact_key,
            BillFact.occurred_time,
            BillFact.cash_direction,
            BillFact.amount_value,
            BillFact.amount_scale,
            BillFact.currency_code,
            BillFact.account_code,
            BillFact.counterparty,
            BillFact.summary,
            BillFact.created_time,
            BillFact.updated_time,
        ).where(BillFact.id.in_(fact_ids)).order_by(BillFact.id)).mappings().all()
        return tuple(DefaultProjectionFactVO(**row) for row in rows)

    def nature_evidence(
        self,
        fact_ids: list[int],
    ) -> tuple[FactNatureEvidenceVO, ...]:
        if not fact_ids:
            return ()
        rows = self.db.execute(select(
            BillRaw.bill_id,
            BillRaw.raw_payload,
            BillRaw.raw_hash,
        ).where(
            BillRaw.bill_id.in_(fact_ids),
            BillRaw.parse_status == "SUCCESS",
        ).order_by(BillRaw.bill_id, BillRaw.id)).mappings().all()
        result = []
        for row in rows:
            try:
                normalized = json.loads(row["raw_payload"]).get("normalized", {})
            except (json.JSONDecodeError, TypeError, AttributeError):
                normalized = {}
            result.append(FactNatureEvidenceVO(
                bill_id=row["bill_id"],
                source_type=str(normalized.get("source_type", "UNKNOWN")),
                nature=str(normalized.get("nature", "ordinary")),
                raw_hash=row["raw_hash"],
            ))
        return tuple(result)

    def financially_reviewed_fact_ids(self, fact_ids: list[int]) -> set[int]:
        if not fact_ids:
            return set()
        return set(self.db.scalars(select(ReviewCaseBill.bill_id).join(
            ReviewCase,
            ReviewCaseBill.case_id == ReviewCase.id,
        ).where(
            ReviewCaseBill.bill_id.in_(fact_ids),
            ReviewCase.status == "CONFIRMED",
            ReviewCase.review_type.in_(FINANCIAL_REVIEW_TYPES),
        )).all())

    def write_defaults(self, values: list[DefaultProjectionWriteVO]) -> dict[int, int]:
        if not values:
            return {}
        fact_ids = [value.fact_id for value in values]
        sources = {
            source.source_id: source
            for source in self.db.scalars(select(LedgerEntrySource).where(
                LedgerEntrySource.source_kind == "BILL_FACT",
                LedgerEntrySource.source_id.in_(fact_ids),
            )).all()
        }
        ledger_ids = [source.ledger_id for source in sources.values()]
        entries = {
            entry.id: entry
            for entry in self.db.scalars(select(LedgerEntry).where(
                LedgerEntry.id.in_(ledger_ids)
            )).all()
        }
        pending_sources = []
        assigned_ledger_ids: set[int] = set()
        for value in values:
            source = sources.get(value.fact_id)
            entry = entries.get(source.ledger_id) if source is not None else None
            if entry is not None and entry.id in assigned_ledger_ids:
                # A revoked financial Review leaves several fact sources on one
                # hot entry. Split them before restoring per-fact defaults.
                entry = None
            fields = {
                "ledger_type": value.ledger_type,
                "allocation_status": value.allocation_status,
                "title": value.title,
                "start_time": value.start_time,
                "end_time": value.end_time,
                "in_amount_value": value.in_amount_value,
                "in_amount_scale": value.in_amount_scale,
                "in_currency_code": value.in_currency_code,
                "out_amount_value": value.out_amount_value,
                "out_amount_scale": value.out_amount_scale,
                "out_currency_code": value.out_currency_code,
                "in_account_code": value.in_account_code,
                "out_account_code": value.out_account_code,
                "input_hash": value.input_hash,
                "updated_time": value.updated_time,
            }
            if entry is None:
                entry = LedgerEntry(
                    created_time=value.created_time,
                    projection_version=1,
                    **fields,
                )
                self.db.add(entry)
                pending_sources.append((value, entry, source))
            elif entry.input_hash != value.input_hash:
                for key, item in fields.items():
                    setattr(entry, key, item)
                entry.projection_version += 1
            if entry.id is not None:
                assigned_ledger_ids.add(entry.id)
        self.db.flush()
        for value, entry, source in pending_sources:
            if source is None:
                self.db.add(LedgerEntrySource(
                    ledger_id=entry.id,
                    source_kind="BILL_FACT",
                    source_id=value.fact_id,
                    created_time=value.created_time,
                    updated_time=value.updated_time,
                ))
            else:
                source.ledger_id = entry.id
                source.updated_time = value.updated_time
        self.db.flush()
        result = {}
        for value in values:
            source = sources.get(value.fact_id)
            if source is not None and source.ledger_id:
                result[value.fact_id] = source.ledger_id
        for value, entry, source in pending_sources:
            result[value.fact_id] = entry.id
        return result
