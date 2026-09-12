from __future__ import annotations

from sqlalchemy.orm import Session

from app.money import cents
from app.schemas.ledger import LedgerPageQuery
from app.schemas.target_ledger import (
    LedgerSummaryBasisRead,
    LedgerSummaryShadowComparisonRead,
    TargetLedgerSummaryQuery,
)
from app.services.dashboard_service import DashboardService
from app.services.target_ledger_summary_service import TargetLedgerSummaryService


class LedgerSummaryShadowComparisonService:
    """Compare the legacy CNY dashboard basis with the target integer summary."""

    def __init__(self, db: Session):
        self.db = db

    def compare(self) -> LedgerSummaryShadowComparisonRead:
        legacy_result = DashboardService(self.db).summary(LedgerPageQuery())
        target_result = TargetLedgerSummaryService(self.db).summary(
            TargetLedgerSummaryQuery()
        )
        legacy = LedgerSummaryBasisRead(
            income_value=cents(legacy_result["income"]),
            expense_value=-cents(legacy_result["spending"]),
            refund_offset_value=cents(legacy_result["refund_offset"]),
            net_value=cents(legacy_result["net"]),
        )
        target_by_currency = {
            item.currency_code: item for item in target_result.totals
        }
        cny = target_by_currency.get("CNY")
        target = LedgerSummaryBasisRead(
            income_value=cny.income_value if cny else 0,
            expense_value=cny.expense_value if cny else 0,
            refund_offset_value=cny.refund_offset_value if cny else 0,
            net_value=cny.net_value if cny else 0,
        )
        differences = []
        foreign_currencies = sorted(set(target_by_currency) - {"CNY"})
        comparable = not foreign_currencies
        if foreign_currencies:
            differences.append(
                "legacy dashboard mixes currencies; target has separate currencies "
                + ",".join(foreign_currencies)
            )
        for field in (
            "income_value",
            "expense_value",
            "refund_offset_value",
            "net_value",
        ):
            legacy_value = getattr(legacy, field)
            target_value = getattr(target, field)
            if legacy_value != target_value:
                differences.append(
                    f"{field}: legacy={legacy_value}, target={target_value}"
                )
        return LedgerSummaryShadowComparisonRead(
            matched=comparable and not differences,
            comparable=comparable,
            legacy_entry_count=legacy_result["bill_count"],
            target_entry_count=target_result.entry_count,
            legacy=legacy,
            target=target,
            differences=differences,
        )
