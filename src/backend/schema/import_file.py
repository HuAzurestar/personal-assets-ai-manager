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
    source_type: str | None = None
    file_format: str | None = None
    status: str | None = None
    created_time_start: datetime | None = None
    created_time_end: datetime | None = None
    updated_time_start: datetime | None = None
    updated_time_end: datetime | None = None


class ImportFileSorter(ListSorter):
    field: Literal["id", "created_time", "updated_time"] = "id"


class ImportFileRead(BaseModel):
    id: int
    batch_code: str
    source_type: str
    institution_code: str
    filename: str
    file_format: str
    sha256: str
    period_start: str
    period_end: str
    total_count: int
    success_count: int
    skip_count: int
    issue_count: int
    status: str
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


class ImportFileDetailRead(BaseModel):
    import_file: ImportFileRead


class ImportFileDetailResponse(SuccessResponse[ImportFileDetailRead]):
    body: ImportFileDetailRead


class ImportFileTransactionFactListRead(BaseModel):
    items: list[TransactionFactListItem]
    total: int


class ImportFileTransactionFactListResponse(
    SuccessResponse[ImportFileTransactionFactListRead]
):
    body: ImportFileTransactionFactListRead


_IMPORT_FILE_DATETIME_ADAPTER = TypeAdapter(datetime)


def parse_import_file_time(value: object, key: str) -> datetime:
    try:
        parsed = _IMPORT_FILE_DATETIME_ADAPTER.validate_python(value)
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
    time_operators: dict[str, set[str]] = {
        "created_time": set(),
        "updated_time": set(),
    }
    allowed_values = {
        "source_type": {"unknown", "manual", "alipay", "wechat", "ccb", "abc", "cmb"},
        "file_format": {"UNKNOWN", "CSV", "XLS", "XLSX", "PDF"},
        "status": {"PENDING", "IMPORTED", "PARTIAL", "FAILED"},
    }
    for expression in fields:
        counts[expression.key] = counts.get(expression.key, 0) + 1
        value = expression.val
        if expression.key == "id":
            valid = isinstance(value, int) and not isinstance(value, bool) and value >= 1
        elif expression.key in allowed_values:
            valid = isinstance(value, str) and value in allowed_values[expression.key]
        else:
            time_operators[expression.key].add(expression.op)
            if expression.op == "between":
                between = BetweenValue.model_validate(value)
                start = parse_import_file_time(between.start, expression.key)
                end = parse_import_file_time(between.end, expression.key)
                valid = start < end
            else:
                parse_import_file_time(value, expression.key)
                valid = True
        if not valid:
            raise ListQueryError(
                "Invalid Import File filter value",
                code="LIST_FILTER_VALUE_INVALID",
                details={
                    "component": "filter",
                    "key": expression.key,
                    "value": value,
                },
            )

    duplicate_fields = sorted(
        key for key, count in counts.items()
        if key not in time_operators and count > 1
    )
    invalid_time_fields = sorted(
        key
        for key, operators in time_operators.items()
        if (
            ("between" in operators and len(operators) > 1)
            or len(operators) != counts.get(key, 0)
        )
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
