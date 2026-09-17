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
from backend.schema.transaction_fact import TransactionFactListItem


class ImportFileListRequest(ListRequest):
    @model_validator(mode="after")
    def validate_capabilities(self) -> "ImportFileListRequest":
        validate_list_capabilities(
            self,
            query_fields=(),
            filter_operators={
                "id": ("=",),
                "source_type": ("=",),
                "file_format": ("=",),
                "status": ("=",),
                "created_time": (">=", "<", "between"),
                "updated_time": (">=", "<", "between"),
            },
            sorter_fields=("id", "created_time", "updated_time"),
            logical_operators=("AND",),
            max_sorters=1,
        )
        _validate_import_file_filter_values(self)
        return self


class ImportFileFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    source_type: int | None = None
    file_format: int | None = None
    status: int | None = None
    created_time_start: datetime | None = None
    created_time_end: datetime | None = None
    updated_time_start: datetime | None = None
    updated_time_end: datetime | None = None


class ImportFileSorter(ListSorter):
    field: Literal["id", "created_time", "updated_time"] = "id"


class ImportFileRead(BaseModel):
    id: int
    batch_code: str
    source_type: int
    filename: str
    file_format: int
    sha256: str
    period_start: str
    period_end: str
    total_count: int
    success_count: int
    skip_count: int
    issue_count: int
    status: int
    created_time: datetime
    updated_time: datetime


class ImportFileListBody(ListBody[ImportFileRead]):
    pass


class ImportFileListResponse(ListResponse[ImportFileRead]):
    body: ImportFileListBody


class ImportFileSummaryRead(BaseModel):
    import_file_count: int
    imported_file_count: int
    row_count: int
    success_count: int
    skip_count: int
    issue_count: int


class ImportFileSummaryResponse(SuccessResponse[ImportFileSummaryRead]):
    body: ImportFileSummaryRead


class ImportFileRelationTotal(BaseModel):
    currency_code: str
    entry_direction: int
    amount: int


class ImportFileRelationSummary(BaseModel):
    review_count: int
    allocation_count: int
    ledger_count: int
    totals: list[ImportFileRelationTotal]


class ImportFileDetailRead(BaseModel):
    import_file: ImportFileRead
    relation_summary: ImportFileRelationSummary


class ImportFileDetailResponse(SuccessResponse[ImportFileDetailRead]):
    body: ImportFileDetailRead


class ImportFileTransactionFactListRead(BaseModel):
    items: list[TransactionFactListItem]
    total: int


class ImportFileTransactionFactListResponse(
    SuccessResponse[ImportFileTransactionFactListRead]
):
    body: ImportFileTransactionFactListRead


class ImportFileRowListRequest(ListRequest):
    @model_validator(mode="after")
    def validate_capabilities(self) -> "ImportFileRowListRequest":
        validate_list_capabilities(
            self,
            query_fields=(),
            filter_operators={"row_status": ("=",)},
            sorter_fields=("source_row_number",),
            logical_operators=("AND",),
            max_sorters=1,
        )
        expressions = list(iter_filter_fields(self.filter))
        if len(expressions) > 1:
            raise ListQueryError(
                "Import File Row status may be filtered only once",
                code="LIST_COMBINATION_NOT_SUPPORTED",
                details={"component": "filter", "duplicate_fields": ["row_status"]},
            )
        for expression in expressions:
            value = expression.val
            if not isinstance(value, int) or isinstance(value, bool) or value not in {1, 2, 3}:
                raise ListQueryError(
                    "Invalid Import File Row status",
                    code="LIST_FILTER_VALUE_INVALID",
                    details={"component": "filter", "key": "row_status", "value": value},
                )
        return self


class ImportFileRowRead(BaseModel):
    source_row_number: int
    row_status: int
    source_reference: str
    issue_code: str
    issue_message: str
    raw_payload: str | None = None
    transaction_fact: TransactionFactListItem | None = None


class ImportFileRowListBody(ListBody[ImportFileRowRead]):
    pass


class ImportFileRowListResponse(ListResponse[ImportFileRowRead]):
    body: ImportFileRowListBody


_DATETIME_ADAPTER = TypeAdapter(datetime)


def parse_import_file_time(value: object, key: str) -> datetime:
    try:
        parsed = _DATETIME_ADAPTER.validate_python(value)
    except ValidationError as error:
        raise ListQueryError(
            "Invalid Import File time filter",
            code="LIST_FILTER_VALUE_INVALID",
            details={"component": "filter", "key": key},
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ListQueryError(
            "Import File time filter requires a timezone",
            code="LIST_FILTER_VALUE_INVALID",
            details={"component": "filter", "key": key},
        )
    return parsed


def _validate_import_file_filter_values(request: ImportFileListRequest) -> None:
    fields = list(iter_filter_fields(request.filter))
    counts: dict[str, int] = {}
    time_operators: dict[str, list[str]] = {"created_time": [], "updated_time": []}
    allowed_values = {
        "source_type": {0, 1, 101, 102, 201, 202, 203},
        "file_format": {0, 1, 2, 3, 4},
        "status": {0, 1, 2, 3},
    }
    for expression in fields:
        counts[expression.key] = counts.get(expression.key, 0) + 1
        value = expression.val
        if expression.key == "id":
            valid = isinstance(value, int) and not isinstance(value, bool) and value >= 1
        elif expression.key in allowed_values:
            valid = isinstance(value, int) and not isinstance(value, bool) and value in allowed_values[expression.key]
        else:
            time_operators[expression.key].append(expression.op)
            if expression.op == "between":
                between = BetweenValue.model_validate(value)
                valid = parse_import_file_time(between.start, expression.key) < parse_import_file_time(between.end, expression.key)
            else:
                parse_import_file_time(value, expression.key)
                valid = True
        if not valid:
            raise ListQueryError(
                "Invalid Import File filter value",
                code="LIST_FILTER_VALUE_INVALID",
                details={"component": "filter", "key": expression.key, "value": value},
            )
    duplicate_fields = sorted(
        key for key, count in counts.items() if key not in time_operators and count > 1
    )
    invalid_time_fields = sorted(
        key for key, operators in time_operators.items()
        if (operators.count("between") > 0 and len(operators) > 1) or len(set(operators)) != len(operators)
    )
    if duplicate_fields or invalid_time_fields:
        raise ListQueryError(
            "Filter combination is not supported",
            code="LIST_COMBINATION_NOT_SUPPORTED",
            details={
                "component": "filter",
                "duplicate_fields": duplicate_fields,
                "invalid_time_fields": invalid_time_fields,
            },
        )
