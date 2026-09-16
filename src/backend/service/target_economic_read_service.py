from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from backend.mapper.target_economic_read_mapper import TargetEconomicReadMapper
from backend.schema.target_economic import (
    EconomicAllocationEvidenceRead,
    EconomicCurrencySummaryRead,
    EconomicFactBriefRead,
    EconomicFlowDetailRead,
    EconomicFlowListItem,
    EconomicFlowPageRead,
    EconomicMoneyRead,
    EconomicPageQuery,
    EconomicReviewBriefRead,
    EconomicSummaryQuery,
    EconomicSummaryRead,
    EconomicTagRead,
)


class TargetEconomicReadService:
    def __init__(self, db: Session):
        self.mapper = TargetEconomicReadMapper(db)

    def page(self, query: EconomicPageQuery) -> EconomicFlowPageRead:
        invalid = sorted(set(query.entry_type) - {0, 1, 2})
        if invalid:
            raise ValueError(f"unknown ledger entry types: {invalid}")
        rows, total, tags = self.mapper.page(query)
        return EconomicFlowPageRead(
            items=[self._flow(row, tags.get(row["id"], [])) for row in rows],
            total=total,
            page=query.page,
            page_size=query.page_size,
            filters={
                "date_from": str(query.date_from) if query.date_from else None,
                "date_to": str(query.date_to) if query.date_to else None,
                "entry_type": list(query.entry_type),
                "currency_code": list(query.currency_code),
                "q": query.q,
            },
        )

    def detail(self, economic_id: int) -> EconomicFlowDetailRead | None:
        data = self.mapper.detail(economic_id)
        if data is None:
            return None
        flow, allocations, facts, reviews, tags, tag_versions, account_versions = data
        versions = {tag_versions.get(row["id"], 0) for row in facts}
        if len(versions) > 1:
            raise ValueError("source facts have different tag Review versions")
        return EconomicFlowDetailRead(
            entry=self._flow(flow, tags),
            tag_review_version=versions.pop() if versions else 0,
            allocations=[EconomicAllocationEvidenceRead(
                id=row["id"],
                review_case_id=row["review_case_id"],
                transaction_fact_id=row["transaction_fact_id"],
                ledger_entry_id=row["ledger_entry_id"],
                amount=EconomicMoneyRead(
                    amount_value=row["amount_value"],
                    amount_scale=row["amount_scale"],
                    currency_code=row["currency_code"],
                ),
            ) for row in allocations],
            facts=[EconomicFactBriefRead(
                id=row["id"],
                occurred_time=row["occurred_time"],
                cash_direction=row["cash_direction"],
                amount=EconomicMoneyRead(
                    amount_value=row["amount_value"],
                    amount_scale=row["amount_scale"],
                    currency_code=row["currency_code"],
                ),
                account_code=row["account_code"],
                account_review_version=account_versions.get(row["id"], 0),
                counterparty=row["counterparty"],
                summary=row["summary"],
            ) for row in facts],
            reviews=[EconomicReviewBriefRead(**row) for row in reviews],
        )

    def summary(self, query: EconomicSummaryQuery) -> EconomicSummaryRead:
        rows = self.mapper.summary(query)
        scales: dict[str, int] = {}
        for row in rows:
            currency = row["currency_code"]
            scales[currency] = max(scales.get(currency, row["amount_scale"]), row["amount_scale"])
        totals = defaultdict(lambda: {
            "income_and_expense_in_value": 0,
            "income_and_expense_out_value": 0,
            "internal_transfer_in_value": 0,
            "internal_transfer_out_value": 0,
            "asset_and_liability_in_value": 0,
            "asset_and_liability_out_value": 0,
        })
        for row in rows:
            currency = row["currency_code"]
            value = row["amount_value"] * (10 ** (scales[currency] - row["amount_scale"]))
            direction = row["entry_direction"]
            target = totals[currency]
            suffix = "in_value" if direction == 1 else "out_value"
            prefix = {
                0: "income_and_expense",
                1: "internal_transfer",
                2: "asset_and_liability",
            }[row["entry_type"]]
            target[f"{prefix}_{suffix}"] += value
        return EconomicSummaryRead(
            entry_count=len(rows),
            totals=[EconomicCurrencySummaryRead(
                currency_code=currency,
                amount_scale=scales[currency],
                **values,
            ) for currency, values in sorted(totals.items())],
        )

    @staticmethod
    def _flow(row, tags=()) -> EconomicFlowListItem:
        return EconomicFlowListItem(
            id=row["id"],
            entry_type=row["entry_type"],
            entry_direction=row["entry_direction"],
            amount=EconomicMoneyRead(
                amount_value=row["amount_value"],
                amount_scale=row["amount_scale"],
                currency_code=row["currency_code"],
            ),
            account_code=row["account_code"],
            counterparty_account_ref=row["counterparty_account_ref"],
            occurred_time=row["occurred_time"],
            tags=[EconomicTagRead(**tag) for tag in tags],
        )
