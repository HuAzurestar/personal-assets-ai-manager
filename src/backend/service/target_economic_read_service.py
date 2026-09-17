from __future__ import annotations

from collections import defaultdict
from datetime import timezone

from sqlalchemy.orm import Session

from backend.mapper.target_economic_read_mapper import TargetEconomicReadMapper
from backend.schema.target_economic import (
    EconomicAllocationEvidenceRead,
    EconomicCurrencySummaryRead,
    EconomicFactBriefRead,
    EconomicFlowDetailItem,
    EconomicFlowDetailRead,
    EconomicFlowListBody,
    EconomicFlowListItem,
    EconomicFlowListRequest,
    EconomicMoneyRead,
    EconomicPageQuery,
    EconomicReviewBriefRead,
    EconomicSummaryQuery,
    EconomicSummaryRead,
    EconomicTagRead,
    parse_economic_flow_time,
)
from backend.schema.list_query import BetweenValue, iter_filter_fields


class TargetEconomicReadService:
    ECONOMIC_TYPES = {
        0: "TRANSACTION",
        1: "ACCOUNT_TRANSFER",
        2: "CLAIM",
    }
    CASH_DIRECTIONS = {1: "IN", 2: "OUT"}

    def __init__(self, db: Session):
        self.mapper = TargetEconomicReadMapper(db)

    def page(self, request: EconomicFlowListRequest) -> EconomicFlowListBody:
        query = self._page_query(request)
        rows, total = self.mapper.page(query)
        return EconomicFlowListBody(
            items=[self._flow(row) for row in rows],
            total=total,
            page_index=query.page,
            page_size=query.page_size,
        )

    @staticmethod
    def _page_query(request: EconomicFlowListRequest) -> EconomicPageQuery:
        values: dict[str, object] = {
            "page": request.page_index,
            "page_size": request.page_size,
        }
        type_codes = {
            "TRANSACTION": 0,
            "ACCOUNT_TRANSFER": 1,
            "CLAIM": 2,
        }
        for expression in iter_filter_fields(request.filter):
            if expression.key == "occurred_time":
                if expression.op == "between":
                    between = BetweenValue.model_validate(expression.val)
                    start = parse_economic_flow_time(between.start)
                    end = parse_economic_flow_time(between.end)
                    values["occurred_time_start"] = start.astimezone(
                        timezone.utc
                    ).replace(tzinfo=None)
                    values["occurred_time_end"] = end.astimezone(
                        timezone.utc
                    ).replace(tzinfo=None)
                else:
                    parsed = parse_economic_flow_time(expression.val).astimezone(
                        timezone.utc
                    ).replace(tzinfo=None)
                    values[
                        "occurred_time_start"
                        if expression.op == ">="
                        else "occurred_time_end"
                    ] = parsed
            elif expression.key == "economic_type":
                values["entry_type"] = type_codes[str(expression.val)]
            elif expression.key == "cash_direction":
                values["cash_direction"] = {"IN": 1, "OUT": 2}[
                    str(expression.val)
                ]
            elif expression.key == "currency_code":
                values["currency_code"] = str(expression.val).strip().upper()
            elif expression.key == "account_code":
                values["account_code"] = str(expression.val).strip()
            else:
                values[expression.key] = expression.val
        sorter = request.sorter[0] if request.sorter else None
        values["sort_field"] = sorter.key if sorter else "occurred_time"
        values["sort_order"] = sorter.direction if sorter else "desc"
        return EconomicPageQuery(**values)

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
                account_review_version=1,
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
            "transaction_in_value": 0,
            "transaction_out_value": 0,
            "account_transfer_in_value": 0,
            "account_transfer_out_value": 0,
            "claim_cashflow_in_value": 0,
            "claim_cashflow_out_value": 0,
        })
        for row in rows:
            currency = row["currency_code"]
            value = row["amount_value"] * (10 ** (scales[currency] - row["amount_scale"]))
            direction = row["entry_direction"]
            target = totals[currency]
            suffix = "in_value" if direction == 1 else "out_value"
            prefix = {
                0: "transaction",
                1: "account_transfer",
                2: "claim_cashflow",
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
    def _flow(row) -> EconomicFlowListItem:
        return EconomicFlowListItem(
            id=row["id"],
            economic_type=TargetEconomicReadService.ECONOMIC_TYPES[row["entry_type"]],
            cash_direction=TargetEconomicReadService.CASH_DIRECTIONS[row["entry_direction"]],
            amount=EconomicMoneyRead(
                amount_value=row["amount_value"],
                amount_scale=row["amount_scale"],
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
