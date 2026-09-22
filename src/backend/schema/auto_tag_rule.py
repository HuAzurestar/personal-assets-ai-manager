from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from backend.core.cron_expression import validate_cron_expression
from backend.error import ListQueryError
from backend.schema.list_query import (
    BetweenValue,
    ListRequest,
    ListSorter,
    iter_filter_fields,
    validate_list_capabilities,
)
from backend.schema.response import ListBody, ListResponse, SuccessResponse


class AutoTagMethodConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    model_id: int = Field(strict=True, ge=1)
    prompt: str = Field(min_length=1)

    @field_validator("prompt")
    @classmethod
    def normalize_prompt(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("prompt cannot be blank")
        return normalized


class AutoTagRuleFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    method: Literal[1] = 1
    method_config: AutoTagMethodConfig
    enabled: bool = Field(strict=True)
    cron: str = Field(max_length=200)
    amount_mode: Literal[1, 2, 3] = 1

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("name cannot be blank")
        return normalized

    @field_validator("cron")
    @classmethod
    def normalize_cron(cls, value: str) -> str:
        if not value.strip():
            return ""
        return validate_cron_expression(value)

    @model_validator(mode="after")
    def require_schedule_when_enabled(self) -> "AutoTagRuleFields":
        if self.enabled and not self.cron:
            raise ValueError("enabled rules require a cron expression")
        return self


class AutoTagRuleCreateRequest(AutoTagRuleFields):
    view_id: int = Field(strict=True, ge=1)


class AutoTagRuleUpdateRequest(AutoTagRuleFields):
    expected_updated_time: datetime

    @field_validator("expected_updated_time")
    @classmethod
    def validate_expected_updated_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expected_updated_time requires a timezone")
        return value


class AutoTagRuleRead(AutoTagRuleFields):
    id: int
    view_id: int
    rule_revision: int
    scan_after_ledger_id: int
    scan_epoch: int
    analyzed_count: str
    failed_count: str
    suggested_count: str
    accepted_count: str
    rejected_count: str
    created_time: datetime
    updated_time: datetime


class AutoTagRuleResponse(SuccessResponse[AutoTagRuleRead]):
    body: AutoTagRuleRead


class AutoTagRuleFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    view_id: int | None = None
    enabled: bool | None = None
    method: Literal[1] | None = None
    amount_mode: Literal[1, 2, 3] | None = None
    created_time_start: datetime | None = None
    created_time_end: datetime | None = None
    updated_time_start: datetime | None = None
    updated_time_end: datetime | None = None


class AutoTagRuleSorter(ListSorter):
    field: Literal["id", "created_time", "updated_time"] = "id"


class AutoTagRuleListRequest(ListRequest):
    @model_validator(mode="after")
    def validate_capabilities(self) -> "AutoTagRuleListRequest":
        validate_list_capabilities(
            self,
            query_fields=("name",),
            filter_operators={
                "id": ("=",),
                "view_id": ("=",),
                "enabled": ("=",),
                "method": ("=",),
                "amount_mode": ("=",),
                "created_time": (">=", "<", "between"),
                "updated_time": (">=", "<", "between"),
            },
            sorter_fields=("id", "created_time", "updated_time"),
            logical_operators=("AND",),
            max_sorters=1,
        )
        _validate_filter_values(self)
        return self


class AutoTagRuleListBody(ListBody[AutoTagRuleRead]):
    pass


class AutoTagRuleListResponse(ListResponse[AutoTagRuleRead]):
    body: AutoTagRuleListBody


class AutoTagRuleSummaryRead(BaseModel):
    id: int
    rule_revision: int
    scan_after_ledger_id: int
    scan_epoch: int
    analyzed_count: str
    failed_count: str
    suggested_count: str
    accepted_count: str
    rejected_count: str


class AutoTagRuleSummaryResponse(SuccessResponse[AutoTagRuleSummaryRead]):
    body: AutoTagRuleSummaryRead


class AutoTagCandidateSample(BaseModel):
    ledger_id: int
    reason: Literal["ELIGIBLE"]


class AutoTagCandidatePreviewRead(BaseModel):
    rule_id: int
    mode: Literal["SIMULATED_LOCAL"]
    inspected_count: int
    eligible_count: int
    reason_counts: dict[str, int]
    samples: list[AutoTagCandidateSample]
    scan_after_ledger_id: int
    page_limit: Literal[100]
    sample_limit: Literal[20]
    message: str


class AutoTagCandidatePreviewResponse(SuccessResponse[AutoTagCandidatePreviewRead]):
    body: AutoTagCandidatePreviewRead


_DATETIME_ADAPTER = TypeAdapter(datetime)


def auto_tag_rule_filter(request: AutoTagRuleListRequest) -> AutoTagRuleFilter:
    values: dict[str, object] = {}
    for expression in iter_filter_fields(request.filter):
        if expression.key in {"created_time", "updated_time"}:
            if expression.op == "between":
                between = BetweenValue.model_validate(expression.val)
                values[f"{expression.key}_start"] = _parse_time(
                    between.start,
                    expression.key,
                )
                values[f"{expression.key}_end"] = _parse_time(
                    between.end,
                    expression.key,
                )
            elif expression.op == ">=":
                values[f"{expression.key}_start"] = _parse_time(
                    expression.val,
                    expression.key,
                )
            else:
                values[f"{expression.key}_end"] = _parse_time(
                    expression.val,
                    expression.key,
                )
        else:
            values[expression.key] = expression.val
    return AutoTagRuleFilter.model_validate(values)


def _parse_time(value: object, key: str) -> datetime:
    try:
        parsed = _DATETIME_ADAPTER.validate_python(value)
    except ValidationError as error:
        raise ListQueryError(
            "Invalid auto tag rule time filter",
            code="LIST_FILTER_VALUE_INVALID",
            details={"component": "filter", "key": key},
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ListQueryError(
            "Auto tag rule time filter requires a timezone",
            code="LIST_FILTER_VALUE_INVALID",
            details={"component": "filter", "key": key},
        )
    return parsed.astimezone(timezone.utc)


def _validate_filter_values(request: AutoTagRuleListRequest) -> None:
    fields = list(iter_filter_fields(request.filter))
    counts: dict[str, int] = {}
    time_operators: dict[str, set[str]] = {
        "created_time": set(),
        "updated_time": set(),
    }
    for expression in fields:
        counts[expression.key] = counts.get(expression.key, 0) + 1
        value = expression.val
        if expression.key in {"id", "view_id"}:
            valid = isinstance(value, int) and not isinstance(value, bool) and value >= 1
        elif expression.key == "enabled":
            valid = isinstance(value, bool)
        elif expression.key == "method":
            valid = (
                isinstance(value, int)
                and not isinstance(value, bool)
                and value == 1
            )
        elif expression.key == "amount_mode":
            valid = (
                isinstance(value, int)
                and not isinstance(value, bool)
                and value in {1, 2, 3}
            )
        else:
            time_operators[expression.key].add(expression.op)
            if expression.op == "between":
                between = BetweenValue.model_validate(value)
                valid = _parse_time(between.start, expression.key) < _parse_time(
                    between.end,
                    expression.key,
                )
            else:
                _parse_time(value, expression.key)
                valid = True
        if not valid:
            raise ListQueryError(
                "Invalid auto tag rule filter value",
                code="LIST_FILTER_VALUE_INVALID",
                details={"component": "filter", "key": expression.key},
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
            "Auto tag rule filter combination is not supported",
            code="LIST_COMBINATION_NOT_SUPPORTED",
            details={
                "component": "filter",
                "duplicate_fields": duplicate_fields,
                "invalid_time_fields": invalid_time_fields,
            },
        )
