from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from backend.mapper.target_economic_read_mapper import TargetEconomicReadMapper
from backend.schema.target_economic import (
    EconomicAllocationEvidenceRead,
    EconomicCurrencySummaryRead,
    EconomicFactBriefRead,
    EconomicFlowDetailItem,
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
    ECONOMIC_TYPES = {
        0: "TRANSACTION",
        1: "ACCOUNT_TRANSFER",
        2: "CLAIM",
    }
    CASH_DIRECTIONS = {1: "IN", 2: "OUT"}

    def __init__(self, db: Session):
        self.mapper = TargetEconomicReadMapper(db)

    def page(self, query: EconomicPageQuery) -> EconomicFlowPageRead:
        invalid = sorted(set(query.entry_type) - {0, 1, 2})
        if invalid:
            raise ValueError(f"unknown ledger entry types: {invalid}")
        rows, total = self.mapper.page(query)
        return EconomicFlowPageRead(
            items=[self._flow(row) for row in rows],
            total=total,
            page=query.page,
            page_size=query.page_size,
            q=query.q,
            filter={
                "date_from": query.date_from,
                "date_to": query.date_to,
                "economic_type": [self.ECONOMIC_TYPES[value] for value in query.entry_type],
                "cash_direction": self.CASH_DIRECTIONS.get(query.cash_direction),
                "currency_code": list(query.currency_code),
                "account_code": query.account_code or None,
            },
            sorter={"field": query.sort_field, "order": query.sort_order},
        )

    def detail(self, economic_id: int) -> EconomicFlowDetailRead | None:
        data = self.mapper.detail(economic_id)
        if data is None:
            return None
        flow, allocations, facts, reviews, tags = data
        return EconomicFlowDetailRead(
            flow=EconomicFlowDetailItem(
                **self._flow(flow).model_dump(),
                tags=[EconomicTagRead(**tag) for tag in tags],
            ),
            allocations=[EconomicAllocationEvidenceRead(
                id=row["id"],
                review_id=row["review_id"],
                fact_id=row["fact_id"],
                economic_id=row["economic_id"],
                amount=EconomicMoneyRead(
                    amount=row["amount"],
                    currency_code=row["currency_code"],
                ),
            ) for row in allocations],
            facts=[EconomicFactBriefRead(
                id=row["id"],
                occurred_time=row["occurred_time"],
                cash_direction=row["cash_direction"],
                amount=EconomicMoneyRead(
                    amount=row["amount"],
                    currency_code=row["currency_code"],
                ),
                account_code=row["account_code"],
                account_review_version=1,
                counterparty=row["counterparty"],
                summary=row["summary"],
            ) for row in facts],
            reviews=[EconomicReviewBriefRead(**row) for row in reviews],
        )

    def summary(self, query: EconomicSummaryQuery) -> EconomicSummaryRead:
        rows = self.mapper.summary(query)
        totals = defaultdict(lambda: {
            "transaction_in_value": 0,
            "transaction_out_value": 0,
            "account_transfer_in_value": 0,
            "account_transfer_out_value": 0,
            "claim_cashflow_in_value": 0,
            "claim_cashflow_out_value": 0,
        })
        for row in rows:
            currency = row["currency_code"]
            direction = row["entry_direction"]
            target = totals[currency]
            suffix = "in_value" if direction == 1 else "out_value"
            prefix = {
                0: "transaction",
                1: "account_transfer",
                2: "claim_cashflow",
            }[row["entry_type"]]
            target[f"{prefix}_{suffix}"] += row["amount"]
        return EconomicSummaryRead(
            entry_count=len(rows),
            totals=[EconomicCurrencySummaryRead(
                currency_code=currency,
                **values,
            ) for currency, values in sorted(totals.items())],
        )

    @staticmethod
    def _flow(row) -> EconomicFlowListItem:
        return EconomicFlowListItem(
            id=row["id"],
            economic_type=TargetEconomicReadService.ECONOMIC_TYPES[row["entry_type"]],
            cash_direction=TargetEconomicReadService.CASH_DIRECTIONS[row["entry_direction"]],
            amount=EconomicMoneyRead(
                amount=row["amount"],
                currency_code=row["currency_code"],
            ),
            account_code=row["account_code"],
            counterparty_account_ref=row["counterparty_account_ref"],
            projection_version=TargetEconomicReadService.projection_version(
                row["updated_time"]
            ),
            occurred_time=row["occurred_time"],
        )

    @staticmethod
    def projection_version(updated_time) -> int:
        """Expose DB-owned updated_time as the Router optimistic-lock token."""

        return max(1, int(updated_time.timestamp() * 1_000_000))
