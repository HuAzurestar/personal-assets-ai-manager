from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from backend.error import ListQueryError
from backend.schema.list_query import (
    BetweenValue,
    ListRequest,
    ListSorter,
    iter_filter_fields,
    validate_list_capabilities,
)
from backend.schema.response import ListBody, ListResponse, SuccessResponse


class LedgerEntryListRequest(ListRequest):
    @model_validator(mode="after")
    def validate_capabilities(self) -> "LedgerEntryListRequest":
        validate_list_capabilities(
            self,
            query_fields=(),
            filter_operators={
                "id": ("=",),
                "occurred_time": (">=", "<", "between"),
                "entry_type": ("=",),
                "entry_direction": ("=",),
                "currency_code": ("=",),
                "account_code": ("=",),
            },
            sorter_fields=("occurred_time", "amount"),
            logical_operators=("AND",),
            max_sorters=1,
        )
        _validate_ledger_entry_filter_values(self)
        return self


class LedgerEntryFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    entry_type: int | None = None
    entry_direction: int | None = None
    currency_code: str | None = None
    account_code: str | None = None
    occurred_time_start: datetime | None = None
    occurred_time_end: datetime | None = None


class LedgerEntrySorter(ListSorter):
    field: Literal["occurred_time", "amount"] = "occurred_time"


@dataclass(frozen=True, slots=True)
class LedgerEntrySummaryQuery:
    date_from: date | None = None
    date_to: date | None = None


class LedgerEntryTagRead(BaseModel):
    view_name: str
    view_system_name: str
    tag_name: str
    tag_system_name: str


class LedgerEntryListItem(BaseModel):
    id: int
    summary: str
    review_behavior_type: int
    entry_type: int
    entry_direction: int
    amount: int
    currency_code: str
    account_code: str
    counterparty_account_ref: str
    occurred_time: datetime
    created_time: datetime
    updated_time: datetime
    tags: list[LedgerEntryTagRead] = Field(default_factory=list)


class LedgerEntryListBody(ListBody[LedgerEntryListItem]):
    pass


class LedgerEntryListResponse(ListResponse[LedgerEntryListItem]):
    body: LedgerEntryListBody


class LedgerAllocationEvidenceRead(BaseModel):
    id: int
    review_case_id: int
    transaction_fact_id: int
    ledger_entry_id: int
    amount: int
    currency_code: str


class LedgerFactBriefRead(BaseModel):
    id: int
    occurred_time: datetime
    cash_direction: int
    amount: int
    currency_code: str
    account_code: str
    counterparty_name: str
    counterparty_account_ref: str
    summary: str


class LedgerReviewBriefRead(BaseModel):
    id: int
    behavior_type: int
    status: int
    title: str
    created_time: datetime
    updated_time: datetime


class LedgerEntryDetailItem(LedgerEntryListItem):
    pass


class LedgerEntryDetailRead(BaseModel):
    ledger_entry: LedgerEntryDetailItem
    allocations: list[LedgerAllocationEvidenceRead]
    facts: list[LedgerFactBriefRead]
    reviews: list[LedgerReviewBriefRead]


class LedgerEntryDetailResponse(SuccessResponse[LedgerEntryDetailRead]):
    body: LedgerEntryDetailRead


class LedgerCurrencySummaryRead(BaseModel):
    currency_code: str
    income_and_expense_in_amount: int
    income_and_expense_out_amount: int
    internal_transfer_in_amount: int
    internal_transfer_out_amount: int
    asset_and_liability_in_amount: int
    asset_and_liability_out_amount: int


class LedgerDailySummaryRead(BaseModel):
    day: date
    currency_code: str
    income_amount: int
    expense_amount: int
    net_amount: int


class LedgerActivitySummaryRead(BaseModel):
    entry_type_code: int
    currency_code: str
    in_amount: int
    out_amount: int
    nettable: bool = True


class LedgerEntrySummaryRead(BaseModel):
    entry_count: int
    totals: list[LedgerCurrencySummaryRead]
    trend: list[LedgerDailySummaryRead]
    activities: list[LedgerActivitySummaryRead]


class LedgerEntrySummaryResponse(SuccessResponse[LedgerEntrySummaryRead]):
    body: LedgerEntrySummaryRead


_DATETIME_ADAPTER = TypeAdapter(datetime)


def parse_ledger_entry_time(value: object) -> datetime:
    try:
        parsed = _DATETIME_ADAPTER.validate_python(value)
    except ValidationError as error:
        raise ListQueryError(
            "Invalid Ledger time filter",
            code="LIST_FILTER_VALUE_INVALID",
            details={"component": "filter", "key": "occurred_time"},
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ListQueryError(
            "Ledger time filter requires a timezone",
            code="LIST_FILTER_VALUE_INVALID",
            details={"component": "filter", "key": "occurred_time"},
        )
    return parsed


def _validate_ledger_entry_filter_values(request: LedgerEntryListRequest) -> None:
    fields = list(iter_filter_fields(request.filter))
    counts: dict[str, int] = {}
    time_operators: list[str] = []
    for expression in fields:
        counts[expression.key] = counts.get(expression.key, 0) + 1
        value = expression.val
        if expression.key == "id":
            valid = isinstance(value, int) and not isinstance(value, bool) and value >= 1
        elif expression.key == "occurred_time":
            time_operators.append(expression.op)
            if expression.op == "between":
                between = BetweenValue.model_validate(value)
                valid = parse_ledger_entry_time(between.start) < parse_ledger_entry_time(between.end)
            else:
                parse_ledger_entry_time(value)
                valid = True
        elif expression.key == "entry_type":
            valid = isinstance(value, int) and not isinstance(value, bool) and value in {0, 1, 2}
        elif expression.key == "entry_direction":
            valid = isinstance(value, int) and not isinstance(value, bool) and value in {1, 2}
        elif expression.key == "currency_code":
            valid = isinstance(value, str) and 1 <= len(value.strip()) <= 12
        else:
            valid = isinstance(value, str) and 1 <= len(value.strip()) <= 120
        if not valid:
            raise ListQueryError(
                "Invalid Ledger filter value",
                code="LIST_FILTER_VALUE_INVALID",
                details={"component": "filter", "key": expression.key, "value": value},
            )
    duplicate_fields = sorted(key for key, count in counts.items() if key != "occurred_time" and count > 1)
    invalid_time_combination = (time_operators.count("between") > 0 and len(time_operators) > 1) or len(set(time_operators)) != len(time_operators)
    if duplicate_fields or invalid_time_combination:
        raise ListQueryError(
            "Filter combination is not supported",
            code="LIST_COMBINATION_NOT_SUPPORTED",
            details={"component": "filter", "duplicate_fields": duplicate_fields, "time_operators": sorted(time_operators)},
        )
