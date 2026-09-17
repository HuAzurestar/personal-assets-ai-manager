from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError, model_validator

from backend.error import ListQueryError
from backend.schema.list_query import (
    BetweenValue,
    ListRequest,
    ListSorter,
    iter_filter_fields,
    validate_list_capabilities,
)
from backend.schema.response import ListBody, ListResponse, SuccessResponse


class TransactionFactFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    cash_direction: Literal["IN", "OUT"] | None = None
    currency_code: str | None = None
    account_code: str | None = None
    amount_scale: int | None = None
    occurred_time_start: datetime | None = None
    occurred_time_end: datetime | None = None


class TransactionFactSorter(ListSorter):
    field: Literal["occurred_time", "amount_value"] = "occurred_time"


class TransactionFactListRequest(ListRequest):
    @model_validator(mode="after")
    def validate_capabilities(self) -> "TransactionFactListRequest":
        validate_list_capabilities(
            self,
            query_fields=(),
            filter_operators={
                "id": ("=",),
                "occurred_time": (">=", "<", "between"),
                "cash_direction": ("=",),
                "currency_code": ("=",),
                "account_code": ("=",),
                "amount_scale": ("=",),
            },
            sorter_fields=("occurred_time", "amount_value"),
            logical_operators=("AND",),
            max_sorters=1,
        )
        _validate_transaction_fact_filter_values(self)
        return self


class TransactionFactListItem(BaseModel):
    id: int
    occurred_time: datetime
    cash_direction: Literal["IN", "OUT"]
    amount_value: int
    amount_scale: int
    currency_code: str
    account_code: str
    counterparty: str
    summary: str
    created_time: datetime
    updated_time: datetime


class TransactionFactRead(TransactionFactListItem):
    fact_key: str


class TransactionFactListBody(ListBody[TransactionFactListItem]):
    pass


class TransactionFactListResponse(ListResponse[TransactionFactListItem]):
    body: TransactionFactListBody


class TransactionFactImportEvidenceRead(BaseModel):
    raw_id: int
    import_file_id: int
    source_row_number: int
    source_reference: str
    parse_status: str
    issue_code: str
    filename: str
    source_type: str
    institution_code: str
    file_format: str
    imported_time: datetime


class TransactionFactAllocationRead(BaseModel):
    id: int
    review_id: int
    fact_id: int
    economic_id: int
    amount_value: int
    amount_scale: int
    currency_code: str


class TransactionFactReviewRead(BaseModel):
    id: int
    review_type: str
    behavior_code: str
    status: str
    version: int
    title: str
    created_time: datetime
    updated_time: datetime


class TransactionFactLedgerRead(BaseModel):
    id: int
    economic_type: Literal["TRANSACTION", "ACCOUNT_TRANSFER", "CLAIM"]
    cash_direction: Literal["IN", "OUT"]
    amount_value: int
    amount_scale: int
    currency_code: str
    account_code: str
    occurred_time: datetime


class TransactionFactDetailRead(BaseModel):
    transaction_fact: TransactionFactRead
    import_evidence: list[TransactionFactImportEvidenceRead]
    allocations: list[TransactionFactAllocationRead]
    reviews: list[TransactionFactReviewRead]
    ledgers: list[TransactionFactLedgerRead]


class TransactionFactDetailResponse(SuccessResponse[TransactionFactDetailRead]):
    body: TransactionFactDetailRead


_DATETIME_ADAPTER = TypeAdapter(datetime)


def parse_transaction_fact_time(value: object) -> datetime:
    try:
        parsed = _DATETIME_ADAPTER.validate_python(value)
    except ValidationError as error:
        raise ListQueryError(
            "Invalid Transaction Fact time filter",
            code="LIST_FILTER_VALUE_INVALID",
            details={"component": "filter", "key": "occurred_time"},
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ListQueryError(
            "Transaction Fact time filter requires a timezone",
            code="LIST_FILTER_VALUE_INVALID",
            details={"component": "filter", "key": "occurred_time"},
        )
    return parsed


def _validate_transaction_fact_filter_values(
    request: TransactionFactListRequest,
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
                start = parse_transaction_fact_time(between.start)
                end = parse_transaction_fact_time(between.end)
                valid = start < end
            else:
                parse_transaction_fact_time(value)
                valid = True
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
                "Invalid Transaction Fact filter value",
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
