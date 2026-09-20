from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from backend.mapper.ledger_entry_mapper import LedgerEntryMapper
from backend.schema.list_query import BetweenValue, iter_filter_fields
from backend.schema.ledger_entry import (
    LedgerActivitySummaryRead,
    LedgerAllocationEvidenceRead,
    LedgerCurrencySummaryRead,
    LedgerDailySummaryRead,
    LedgerEntryDetailItem,
    LedgerEntryDetailRead,
    LedgerEntryFilter,
    LedgerEntryListBody,
    LedgerEntryListItem,
    LedgerEntryListRequest,
    LedgerEntrySorter,
    LedgerEntrySummaryQuery,
    LedgerEntrySummaryRead,
    LedgerEntryTagRead,
    LedgerFactBriefRead,
    LedgerReviewBriefRead,
    parse_ledger_entry_time,
)


class LedgerEntryService:
    def __init__(self, db: Session):
        self.mapper = LedgerEntryMapper(db)

    def page(self, *, request: LedgerEntryListRequest) -> LedgerEntryListBody:
        filter_value = self._mapper_filter(request)
        sorter_expression = request.sorter[0] if request.sorter else None
        sorter = LedgerEntrySorter(
            field=sorter_expression.key if sorter_expression else "occurred_time",
            order=sorter_expression.direction if sorter_expression else "desc",
        )
        rows, total = self.mapper.page(
            page=request.page_index,
            page_size=request.page_size,
            filter_value=filter_value,
            sorter=sorter,
        )
        tags_by_ledger = self.mapper.tags([row["id"] for row in rows])
        items = []
        for row in rows:
            values = dict(row)
            values["summary"] = self._summary(
                values["summary"], values["review_behavior_type"]
            )
            items.append(LedgerEntryListItem(
                **values,
                tags=[
                    LedgerEntryTagRead(**tag)
                    for tag in tags_by_ledger.get(row["id"], [])
                ],
            ))
        return LedgerEntryListBody(
            items=items,
            total=total,
            page_index=request.page_index,
            page_size=request.page_size,
        )

    @staticmethod
    def _mapper_filter(request: LedgerEntryListRequest) -> LedgerEntryFilter:
        values: dict[str, object] = {}
        for expression in iter_filter_fields(request.filter):
            if expression.key == "occurred_time":
                if expression.op == "between":
                    between = BetweenValue.model_validate(expression.val)
                    values["occurred_time_start"] = parse_ledger_entry_time(between.start)
                    values["occurred_time_end"] = parse_ledger_entry_time(between.end)
                elif expression.op == ">=":
                    values["occurred_time_start"] = parse_ledger_entry_time(expression.val)
                else:
                    values["occurred_time_end"] = parse_ledger_entry_time(expression.val)
            elif expression.key in {"currency_code", "account_code"}:
                values[expression.key] = str(expression.val).strip()
            else:
                values[expression.key] = expression.val
        return LedgerEntryFilter.model_validate(values)

    def detail(self, ledger_id: int) -> LedgerEntryDetailRead | None:
        data = self.mapper.detail(ledger_id)
        if data is None:
            return None
        ledger_entry, allocations, facts, reviews, tags = data
        return LedgerEntryDetailRead(
            ledger_entry=LedgerEntryDetailItem(
                **ledger_entry,
                summary=self._summary(
                    facts[0]["summary"], reviews[0]["behavior_type"]
                ),
                review_behavior_type=reviews[0]["behavior_type"],
                tags=[LedgerEntryTagRead(**tag) for tag in tags],
            ),
            allocations=[LedgerAllocationEvidenceRead(**row) for row in allocations],
            facts=[LedgerFactBriefRead(**row) for row in facts],
            reviews=[LedgerReviewBriefRead(**row) for row in reviews],
        )

    @staticmethod
    def _summary(fact_summary: str, review_behavior_type: int) -> str:
        behavior = {
            0: "事实交易",
            1: "借款与还款",
        }.get(review_behavior_type, f"审查类型 {review_behavior_type}")
        summary = fact_summary.strip()
        return f"{behavior}：{summary}" if summary else behavior

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
        trend = defaultdict(lambda: {"income_amount": 0, "expense_amount": 0})
        activities = defaultdict(lambda: {"in_amount": 0, "out_amount": 0})
        for row in rows:
            direction = "in" if row["entry_direction"] == 1 else "out"
            key = f"{type_prefixes[row['entry_type']]}_{direction}_amount"
            totals[row["currency_code"]][key] += row["amount"]
            activity_key = (row["entry_type"], row["currency_code"])
            activities[activity_key][f"{direction}_amount"] += row["amount"]
            if row["entry_type"] == 0:
                local_time = (
                    row["occurred_time"].astimezone(query.display_timezone)
                    if query.display_timezone is not None
                    else row["occurred_time"]
                )
                day_key = (local_time.date(), row["currency_code"])
                trend[day_key]["income_amount" if direction == "in" else "expense_amount"] += row["amount"]
        return LedgerEntrySummaryRead(
            entry_count=len(rows),
            totals=[
                LedgerCurrencySummaryRead(currency_code=currency, **values)
                for currency, values in sorted(totals.items())
            ],
            trend=[
                LedgerDailySummaryRead(
                    day=day,
                    currency_code=currency,
                    net_amount=values["income_amount"] - values["expense_amount"],
                    **values,
                )
                for (day, currency), values in sorted(trend.items())
            ],
            activities=[
                LedgerActivitySummaryRead(
                    entry_type_code=entry_type,
                    currency_code=currency,
                    **values,
                )
                for (entry_type, currency), values in sorted(activities.items())
            ],
        )
