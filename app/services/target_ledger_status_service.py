from __future__ import annotations

from dataclasses import asdict

from sqlalchemy.orm import Session

from app.mappers.target_ledger_mapper import TargetLedgerMapper
from app.schemas.target_ledger import TargetLedgerShadowStatusRead
from app.services.ledger_summary_comparison_service import (
    LedgerSummaryShadowComparisonService,
)


class TargetLedgerStatusService:
    """Read-only dual-read gate for exposing the target ledger."""

    def __init__(self, db: Session):
        self.mapper = TargetLedgerMapper(db)
        self.summary_comparison = LedgerSummaryShadowComparisonService(db)

    def status(self) -> TargetLedgerShadowStatusRead:
        counts = self.mapper.readiness()
        summary = self.summary_comparison.compare()
        blockers = []
        if counts.fact_count != counts.legacy_bill_count:
            blockers.append(
                f"fact coverage: legacy={counts.legacy_bill_count}, target={counts.fact_count}"
            )
        if counts.fact_source_count != counts.fact_count:
            blockers.append(
                f"fact lineage: facts={counts.fact_count}, sources={counts.fact_source_count}"
            )
        if counts.review_source_count != counts.confirmed_review_count:
            blockers.append(
                "review lineage: "
                f"confirmed={counts.confirmed_review_count}, sources={counts.review_source_count}"
            )
        expected_tags = counts.ledger_entry_count * counts.active_tag_view_count
        if counts.ledger_tag_count != expected_tags:
            blockers.append(
                f"tag coverage: expected={expected_tags}, target={counts.ledger_tag_count}"
            )
        if counts.legacy_bill_count and not counts.ledger_entry_count:
            blockers.append("ledger projection is empty while legacy bills exist")
        if not summary.matched:
            blockers.extend(f"summary: {item}" for item in summary.differences)
            if not summary.differences:
                blockers.append("summary comparison did not match")
        return TargetLedgerShadowStatusRead(
            ready=not blockers,
            **asdict(counts),
            summary=summary,
            blockers=blockers,
        )
