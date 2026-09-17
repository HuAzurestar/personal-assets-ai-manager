from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_validator

from backend.error import ListQueryError
from backend.schema.list_query import (
    BetweenValue,
    ListRequest,
    iter_filter_fields,
    validate_list_capabilities,
)
from backend.schema.response import ListBody, ListResponse, SuccessResponse


class EconomicFlowListRequest(ListRequest):
    @model_validator(mode="after")
    def validate_capabilities(self) -> "EconomicFlowListRequest":
        validate_list_capabilities(
            self,
            query_fields=(),
            filter_operators={
                "id": ("=",),
                "occurred_time": (">=", "<", "between"),
                "economic_type": ("=",),
                "cash_direction": ("=",),
                "currency_code": ("=",),
                "account_code": ("=",),
                "amount_scale": ("=",),
            },
            sorter_fields=("occurred_time", "amount_value"),
            logical_operators=("AND",),
            max_sorters=1,
        )
        _validate_economic_flow_filter_values(self)
        return self


@dataclass(frozen=True, slots=True)
class EconomicPageQuery:
    page: int = 1
    page_size: int = 20
    id: int | None = None
    occurred_time_start: datetime | None = None
    occurred_time_end: datetime | None = None
    entry_type: int | None = None
    currency_code: str = ""
    cash_direction: int | None = None
    account_code: str = ""
    amount_scale: int | None = None
    sort_field: str = "occurred_time"
    sort_order: str = "desc"


@dataclass(frozen=True, slots=True)
class EconomicSummaryQuery:
    date_from: date | None = None
    date_to: date | None = None


class EconomicMoneyRead(BaseModel):
    amount_value: int
    amount_scale: int
    currency_code: str


class EconomicTagRead(BaseModel):
    view_name: str
    view_system_name: str
    tag_name: str
    tag_system_name: str


class EconomicFlowListItem(BaseModel):
    id: int
    economic_type: Literal["TRANSACTION", "ACCOUNT_TRANSFER", "CLAIM"]
    cash_direction: Literal["IN", "OUT"]
    amount: EconomicMoneyRead
    account_code: str
    counterparty_account_ref: str
    projection_version: int
    occurred_time: datetime


class EconomicFlowListBody(ListBody[EconomicFlowListItem]):
    pass


class EconomicFlowListResponse(ListResponse[EconomicFlowListItem]):
    body: EconomicFlowListBody


class EconomicAllocationEvidenceRead(BaseModel):
    id: int
    review_id: int
    fact_id: int
    economic_id: int
    amount: EconomicMoneyRead


class EconomicFactBriefRead(BaseModel):
    id: int
    occurred_time: datetime
    cash_direction: str
    amount: EconomicMoneyRead
    account_code: str
    account_review_version: int
    counterparty: str
    summary: str


class EconomicReviewBriefRead(BaseModel):
    id: int
    review_type: str
    behavior_code: str
    status: str
    version: int
    title: str


class EconomicFlowDetailItem(EconomicFlowListItem):
    tags: list[EconomicTagRead] = Field(default_factory=list)


class EconomicFlowDetailRead(BaseModel):
    flow: EconomicFlowDetailItem
    allocations: list[EconomicAllocationEvidenceRead]
    facts: list[EconomicFactBriefRead]
    reviews: list[EconomicReviewBriefRead]


class EconomicFlowDetailResponse(SuccessResponse[EconomicFlowDetailRead]):
    body: EconomicFlowDetailRead


class EconomicCurrencySummaryRead(BaseModel):
    currency_code: str
    amount_scale: int
    transaction_in_value: int
    transaction_out_value: int
    account_transfer_in_value: int
    account_transfer_out_value: int
    claim_cashflow_in_value: int
    claim_cashflow_out_value: int


class EconomicSummaryRead(BaseModel):
    entry_count: int
    totals: list[EconomicCurrencySummaryRead]
    basis_version: str = "economic-flow-v1"


class EconomicSummaryResponse(SuccessResponse[EconomicSummaryRead]):
    body: EconomicSummaryRead


_DATETIME_ADAPTER = TypeAdapter(datetime)


def parse_economic_flow_time(value: object) -> datetime:
    try:
        parsed = _DATETIME_ADAPTER.validate_python(value)
    except ValidationError as error:
        raise ListQueryError(
            "Invalid Ledger occurred_time filter",
            code="LIST_FILTER_VALUE_INVALID",
            details={"component": "filter", "key": "occurred_time"},
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ListQueryError(
            "Ledger occurred_time filter requires a timezone",
            code="LIST_FILTER_VALUE_INVALID",
            details={"component": "filter", "key": "occurred_time"},
        )
    return parsed


def _validate_economic_flow_filter_values(
    request: EconomicFlowListRequest,
) -> None:
    fields = list(iter_filter_fields(request.filter))
    counts: dict[str, int] = {}
    time_operators: set[str] = set()
    for expression in fields:
        counts[expression.key] = counts.get(expression.key, 0) + 1
        value = expression.val
        if expression.key == "id":
            valid = isinstance(value, int) and not isinstance(value, bool) and value >= 1
        elif expression.key == "occurred_time":
            time_operators.add(expression.op)
            if expression.op == "between":
                between = BetweenValue.model_validate(value)
                start = parse_economic_flow_time(between.start)
                end = parse_economic_flow_time(between.end)
                valid = start < end
            else:
                parse_economic_flow_time(value)
                valid = True
        elif expression.key == "economic_type":
            valid = isinstance(value, str) and value in {
                "TRANSACTION",
                "ACCOUNT_TRANSFER",
                "CLAIM",
            }
        elif expression.key == "cash_direction":
            valid = isinstance(value, str) and value in {"IN", "OUT"}
        elif expression.key == "currency_code":
            valid = isinstance(value, str) and 1 <= len(value.strip()) <= 12
        elif expression.key == "account_code":
            valid = isinstance(value, str) and 1 <= len(value.strip()) <= 120
        else:
            valid = (
                isinstance(value, int)
                and not isinstance(value, bool)
                and 0 <= value <= 8
            )
        if not valid:
            raise ListQueryError(
                "Invalid Ledger filter value",
                code="LIST_FILTER_VALUE_INVALID",
                details={
                    "component": "filter",
                    "key": expression.key,
                    "value": value,
                },
            )
    duplicate_fields = sorted(
        key for key, count in counts.items() if key != "occurred_time" and count > 1
    )
    invalid_time_combination = (
        "between" in time_operators and len(time_operators) > 1
    ) or len(time_operators) != counts.get("occurred_time", 0)
    if duplicate_fields or invalid_time_combination:
        raise ListQueryError(
            "Filter combination is not supported",
            code="LIST_COMBINATION_NOT_SUPPORTED",
            details={
                "component": "filter",
                "duplicate_fields": duplicate_fields,
                "time_operators": sorted(time_operators),
            },
        )
