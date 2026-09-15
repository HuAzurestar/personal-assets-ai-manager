from __future__ import annotations

from collections import defaultdict

from sqlalchemy.orm import Session

from backend.mapper.target_ledger_mapper import TargetLedgerMapper
from backend.schema.target_ledger import (
    TargetCurrencySummaryRead,
    TargetLedgerActivityRead,
    TargetLedgerSummaryQuery,
    TargetLedgerSummaryRead,
    TargetLedgerTrendRead,
)


class TargetLedgerSummaryService:
    """Compute integer, per-currency business totals from the hot projection."""

    def __init__(self, db: Session):
        self.mapper = TargetLedgerMapper(db)

    def summary(self, query: TargetLedgerSummaryQuery) -> TargetLedgerSummaryRead:
        entry_count, provisional_count, rows = self.mapper.summary(query)
        currency_scales: dict[str, int] = {}
        for row in rows:
            currency_scales[row.currency_code] = max(
                currency_scales.get(row.currency_code, row.amount_scale),
                row.amount_scale,
            )

        activities = defaultdict(lambda: {"in": 0, "out": 0})
        entry_activities = defaultdict(lambda: {"in": 0, "out": 0})
        daily_entry_activities = defaultdict(lambda: {"in": 0, "out": 0})
        for row in rows:
            scale = currency_scales[row.currency_code]
            value = row.amount_value * (10 ** (scale - row.amount_scale))
            direction = row.direction.lower()
            activity_key = (row.ledger_type, row.currency_code, row.nettable)
            entry_key = (row.ledger_id, row.ledger_type, row.currency_code, row.nettable)
            day_key = (row.day, row.ledger_id, row.ledger_type, row.currency_code, row.nettable)
            activities[activity_key][direction] += value
            entry_activities[entry_key][direction] += value
            daily_entry_activities[day_key][direction] += value

        account_scoped = bool(query.account_code)
        totals = self._business_totals(
            entry_activities,
            currency_scales,
            account_scoped=account_scoped,
        )
        daily_totals = self._business_totals(
            daily_entry_activities,
            currency_scales,
            daily=True,
            account_scoped=account_scoped,
        )
        return TargetLedgerSummaryRead(
            entry_count=entry_count,
            provisional_count=provisional_count,
            totals=[TargetCurrencySummaryRead(
                currency_code=currency,
                amount_scale=currency_scales[currency],
                **values,
            ) for currency, values in sorted(totals.items())],
            activities=[TargetLedgerActivityRead(
                ledger_type=ledger_type,
                currency_code=currency,
                amount_scale=currency_scales[currency],
                in_amount_value=values["in"],
                out_amount_value=values["out"],
                nettable=nettable,
            ) for (ledger_type, currency, nettable), values in sorted(activities.items())],
            trend=[TargetLedgerTrendRead(
                day=day,
                currency_code=currency,
                amount_scale=currency_scales[currency],
                **values,
            ) for (day, currency), values in sorted(daily_totals.items())],
        )

    @classmethod
    def _business_totals(
        cls,
        activities,
        currency_scales,
        *,
        daily=False,
        account_scoped=False,
    ):
        totals = defaultdict(lambda: {
            "income_value": 0,
            "expense_value": 0,
            "refund_offset_value": 0,
            "net_value": 0,
        })
        for key, legs in activities.items():
            if daily:
                day, _ledger_id, ledger_type, currency, nettable = key
                total_key = (day, currency)
            else:
                _ledger_id, ledger_type, currency, nettable = key
                total_key = currency
            income = expense = refund = 0
            incoming, outgoing = legs["in"], legs["out"]
            if ledger_type == "INCOME":
                income = incoming
            elif ledger_type == "EXPENSE":
                expense = outgoing
            elif ledger_type == "REFUND":
                expense = outgoing
                refund = incoming
            elif (
                ledger_type in {"AA", "TRANSFER"}
                and nettable
                and not account_scoped
            ):
                income = max(incoming - outgoing, 0)
                expense = max(outgoing - incoming, 0)
            totals[total_key]["income_value"] += income
            totals[total_key]["expense_value"] += expense
            totals[total_key]["refund_offset_value"] += refund
        for values in totals.values():
            values["net_value"] = (
                values["income_value"]
                - values["expense_value"]
                + values["refund_offset_value"]
            )
        for currency in currency_scales:
            if daily:
                continue
            totals[currency]
        return totals
