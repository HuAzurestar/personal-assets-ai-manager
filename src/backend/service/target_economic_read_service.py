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
        invalid = sorted(set(query.economic_type) - {"TRANSACTION", "ACCOUNT_TRANSFER", "CLAIM"})
        if invalid:
            raise ValueError(f"unknown economic types: {invalid}")
        rows, total, tags = self.mapper.page(query)
        return EconomicFlowPageRead(
            items=[self._flow(row, tags.get(row["id"], [])) for row in rows],
            total=total,
            page=query.page,
            page_size=query.page_size,
            filters={
                "date_from": str(query.date_from) if query.date_from else None,
                "date_to": str(query.date_to) if query.date_to else None,
                "economic_type": list(query.economic_type),
                "currency_code": list(query.currency_code),
                "q": query.q,
            },
        )

    def detail(self, economic_id: int) -> EconomicFlowDetailRead | None:
        data = self.mapper.detail(economic_id)
        if data is None:
            return None
        flow, allocations, facts, reviews, tags = data
        return EconomicFlowDetailRead(
            flow=self._flow(flow, tags),
            allocations=[EconomicAllocationEvidenceRead(
                id=row["id"],
                review_id=row["review_id"],
                fact_id=row["fact_id"],
                economic_id=row["economic_id"],
                role=row["role"],
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
            "income_value": 0,
            "expense_value": 0,
            "reversal_in_value": 0,
            "reversal_out_value": 0,
            "account_transfer_in_value": 0,
            "account_transfer_out_value": 0,
            "claim_in_value": 0,
            "claim_out_value": 0,
            "receivable_balance_value": 0,
            "payable_balance_value": 0,
        })
        for row in rows:
            currency = row["currency_code"]
            value = row["amount_value"] * (10 ** (scales[currency] - row["amount_scale"]))
            direction = row["cash_direction"]
            target = totals[currency]
            if row["economic_type"] == "TRANSACTION":
                if row["reversal_of_id"]:
                    target["reversal_in_value" if direction == "IN" else "reversal_out_value"] += value
                elif direction == "IN":
                    target["income_value"] += value
                else:
                    target["expense_value"] += value
            elif row["economic_type"] == "ACCOUNT_TRANSFER":
                target["account_transfer_in_value" if direction == "IN" else "account_transfer_out_value"] += value
            elif row["economic_type"] == "CLAIM":
                target["claim_in_value" if direction == "IN" else "claim_out_value"] += value
                sign = 1 if (
                    (row["claim_side"] == "RECEIVABLE" and direction == "OUT")
                    or (row["claim_side"] == "PAYABLE" and direction == "IN")
                ) else -1
                key = "receivable_balance_value" if row["claim_side"] == "RECEIVABLE" else "payable_balance_value"
                target[key] += sign * value
        return EconomicSummaryRead(
            entry_count=len(rows),
            totals=[EconomicCurrencySummaryRead(
                currency_code=currency,
                amount_scale=scales[currency],
                account_transfer_net_value=(
                    values["account_transfer_in_value"] - values["account_transfer_out_value"]
                ),
                **values,
            ) for currency, values in sorted(totals.items())],
        )

    @staticmethod
    def _flow(row, tags=()) -> EconomicFlowListItem:
        return EconomicFlowListItem(
            id=row["id"],
            economic_type=row["economic_type"],
            cash_direction=row["cash_direction"],
            amount=EconomicMoneyRead(
                amount_value=row["amount_value"],
                amount_scale=row["amount_scale"],
                currency_code=row["currency_code"],
            ),
            title=row["title"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            claim_key=row["claim_key"],
            claim_side=row["claim_side"],
            reversal_of_id=row["reversal_of_id"],
            projection_version=row["projection_version"],
            tags=[EconomicTagRead(**tag) for tag in tags],
        )
