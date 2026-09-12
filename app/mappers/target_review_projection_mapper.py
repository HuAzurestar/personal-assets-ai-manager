from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.models.target import LedgerEntry, LedgerEntrySource, LedgerEntryTag
from app.schemas.target_projection import FinancialProjectionWriteVO


class TargetReviewProjectionMapper:
    """Reconcile one confirmed financial Review into the hot projection."""

    def __init__(self, db: Session):
        self.db = db

    def publish(self, value: FinancialProjectionWriteVO) -> None:
        source_rows = self.db.execute(select(
            LedgerEntrySource.id,
            LedgerEntrySource.ledger_id,
            LedgerEntrySource.source_id,
        ).where(
            LedgerEntrySource.source_kind == "BILL_FACT",
            LedgerEntrySource.source_id.in_(value.fact_ids),
        )).mappings().all()
        source_by_fact = {row["source_id"]: row for row in source_rows}
        missing = sorted(set(value.fact_ids) - set(source_by_fact))
        if missing:
            raise RuntimeError(f"facts have no default projection source: {missing}")

        ledger_ids = sorted({row["ledger_id"] for row in source_rows})
        chosen_id = ledger_ids[0]
        all_sources = self.db.execute(select(
            LedgerEntrySource.ledger_id,
            LedgerEntrySource.source_kind,
            LedgerEntrySource.source_id,
        ).where(LedgerEntrySource.ledger_id.in_(ledger_ids))).mappings().all()
        allowed = {
            ("BILL_FACT", fact_id) for fact_id in value.fact_ids
        } | {("REVIEW_CASE", value.case_id)}
        unexpected = [
            (row["source_kind"], row["source_id"])
            for row in all_sources
            if (row["source_kind"], row["source_id"]) not in allowed
        ]
        if unexpected:
            raise RuntimeError(f"projection component has unexpected sources: {unexpected}")

        self.db.execute(update(LedgerEntrySource).where(
            LedgerEntrySource.source_kind == "BILL_FACT",
            LedgerEntrySource.source_id.in_(value.fact_ids),
        ).values(ledger_id=chosen_id, updated_time=value.updated_time))
        review_source = self.db.execute(select(
            LedgerEntrySource.id,
            LedgerEntrySource.ledger_id,
        ).where(
            LedgerEntrySource.source_kind == "REVIEW_CASE",
            LedgerEntrySource.source_id == value.case_id,
        )).mappings().one_or_none()
        if review_source is None:
            self.db.add(LedgerEntrySource(
                ledger_id=chosen_id,
                source_kind="REVIEW_CASE",
                source_id=value.case_id,
                created_time=value.created_time,
                updated_time=value.updated_time,
            ))
        elif review_source["ledger_id"] != chosen_id:
            self.db.execute(update(LedgerEntrySource).where(
                LedgerEntrySource.id == review_source["id"]
            ).values(ledger_id=chosen_id, updated_time=value.updated_time))

        self.db.execute(update(LedgerEntry).where(
            LedgerEntry.id == chosen_id,
        ).values(
            ledger_type=value.ledger_type,
            allocation_status=value.allocation_status,
            title=value.title,
            start_time=value.start_time,
            end_time=value.end_time,
            in_amount_value=value.in_amount_value,
            in_amount_scale=value.in_amount_scale,
            in_currency_code=value.in_currency_code,
            out_amount_value=value.out_amount_value,
            out_amount_scale=value.out_amount_scale,
            out_currency_code=value.out_currency_code,
            in_account_code=value.in_account_code,
            out_account_code=value.out_account_code,
            input_hash=value.input_hash,
            projection_version=LedgerEntry.projection_version + 1,
            updated_time=value.updated_time,
        ))
        obsolete = ledger_ids[1:]
        if obsolete:
            self.db.execute(delete(LedgerEntryTag).where(
                LedgerEntryTag.ledger_id.in_(obsolete)
            ))
            self.db.execute(delete(LedgerEntry).where(LedgerEntry.id.in_(obsolete)))

    def detach(self, case_id: int) -> None:
        self.db.execute(delete(LedgerEntrySource).where(
            LedgerEntrySource.source_kind == "REVIEW_CASE",
            LedgerEntrySource.source_id == case_id,
        ))
