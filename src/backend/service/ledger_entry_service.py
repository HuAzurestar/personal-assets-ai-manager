from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from backend.mapper.ledger_entry_mapper import LedgerEntryMapper
from backend.schema.ledger_entry import (
    LedgerAllocationEvidenceRead,
    LedgerCurrencySummaryRead,
    LedgerEntryDetailItem,
    LedgerEntryDetailRead,
    LedgerEntryListItem,
    LedgerEntryPageQuery,
    LedgerEntryPageRead,
    LedgerEntrySummaryQuery,
    LedgerEntrySummaryRead,
    LedgerEntryTagRead,
    LedgerFactBriefRead,
    LedgerReviewBriefRead,
)


class LedgerEntryService:
    def __init__(self, db: Session):
        self.mapper = LedgerEntryMapper(db)

    def page(self, query: LedgerEntryPageQuery) -> LedgerEntryPageRead:
        invalid_types = sorted(set(query.entry_type) - {0, 1, 2})
        if invalid_types:
            raise ValueError(f"unknown ledger entry types: {invalid_types}")
        if query.entry_direction not in {None, 1, 2}:
            raise ValueError(
                f"unknown ledger entry direction: {query.entry_direction}"
            )
        rows, total = self.mapper.page(query)
        return LedgerEntryPageRead(
            items=[LedgerEntryListItem(**row) for row in rows],
            total=total,
            page=query.page,
            page_size=query.page_size,
            q=query.q,
            filter={
                "date_from": query.date_from,
                "date_to": query.date_to,
                "entry_type": list(query.entry_type),
                "entry_direction": query.entry_direction,
                "currency_code": list(query.currency_code),
                "account_code": query.account_code or None,
            },
            sorter={"field": query.sort_field, "order": query.sort_order},
        )

    def detail(self, ledger_id: int) -> LedgerEntryDetailRead | None:
        data = self.mapper.detail(ledger_id)
        if data is None:
            return None
        ledger_entry, allocations, facts, reviews, tags = data
        return LedgerEntryDetailRead(
            ledger_entry=LedgerEntryDetailItem(
                **ledger_entry,
                tags=[LedgerEntryTagRead(**tag) for tag in tags],
            ),
            allocations=[LedgerAllocationEvidenceRead(**row) for row in allocations],
            facts=[LedgerFactBriefRead(**row) for row in facts],
            reviews=[LedgerReviewBriefRead(**row) for row in reviews],
        )

    def summary(self, query: LedgerEntrySummaryQuery) -> LedgerEntrySummaryRead:
        rows = self.mapper.summary(query)
        totals = defaultdict(lambda: {
            "income_and_expense_in_amount": 0,
            "income_and_expense_out_amount": 0,
            "internal_transfer_in_amount": 0,
            "internal_transfer_out_amount": 0,
            "asset_and_liability_in_amount": 0,
            "asset_and_liability_out_amount": 0,
        })
        type_prefixes = {
            0: "income_and_expense",
            1: "internal_transfer",
            2: "asset_and_liability",
        }
        for row in rows:
            direction = "in" if row["entry_direction"] == 1 else "out"
            key = f"{type_prefixes[row['entry_type']]}_{direction}_amount"
            totals[row["currency_code"]][key] += row["amount"]
        return LedgerEntrySummaryRead(
            entry_count=len(rows),
            totals=[
                LedgerCurrencySummaryRead(currency_code=currency, **values)
                for currency, values in sorted(totals.items())
            ],
        )

    @staticmethod
    def projection_version(updated_time) -> int:
        """Temporary write-token adapter for account and tag mutation APIs."""

        return max(1, int(updated_time.timestamp() * 1_000_000))
