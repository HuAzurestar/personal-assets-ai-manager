from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from backend.error import ListQueryError


COMPARISON_OPERATORS = (">", ">=", "<", "<=", "=", "!=", "between")
LOGICAL_OPERATORS = ("AND", "OR", "NOT")
SORT_DIRECTIONS = ("asc", "desc")


class QueryExpression(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    word: str = Field(min_length=1, max_length=200)


class BetweenValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: Any
    end: Any


class FilterFieldExpression(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    op: Literal[">", ">=", "<", "<=", "=", "!=", "between"]
    val: Any


class FilterLogicalExpression(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["AND", "OR", "NOT"]
    expression: list["FilterExpression"] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_child_count(self) -> "FilterLogicalExpression":
        if self.op == "NOT" and len(self.expression) != 1:
            raise ValueError("NOT requires exactly one expression")
        if self.op in {"AND", "OR"} and len(self.expression) < 2:
            raise ValueError(f"{self.op} requires at least two expressions")
        return self


FilterExpression = FilterFieldExpression | FilterLogicalExpression
FilterLogicalExpression.model_rebuild()


class SorterExpression(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    direction: Literal["asc", "desc"]


class ListRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_index: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    query: list[QueryExpression] = Field(default_factory=list)
    filter: FilterExpression | None = None
    sorter: list[SorterExpression] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def validate_filter_complexity(self) -> "ListRequest":
        if self.filter is None:
            return self
        depth, fields = _filter_metrics(self.filter)
        if depth > 3 or fields > 20:
            raise ListQueryError(
                "List filter expression limit exceeded",
                code="LIST_EXPRESSION_LIMIT_EXCEEDED",
                details={
                    "component": "filter",
                    "depth": depth,
                    "field_count": fields,
                    "max_depth": 3,
                    "max_field_count": 20,
                },
            )
        return self


ListRequestT = TypeVar("ListRequestT", bound=ListRequest)


_QUERY_ADAPTER = TypeAdapter(list[QueryExpression])
_FILTER_ADAPTER = TypeAdapter(FilterExpression)
_SORTER_ADAPTER = TypeAdapter(list[SorterExpression])


def parse_list_request(
    model: type[ListRequestT],
    *,
    page_index: int = 1,
    page_size: int = 20,
    query: str | None = None,
    filter: str | None = None,
    sorter: str | None = None,
) -> ListRequestT:
    values: dict[str, Any] = {
        "page_index": page_index,
        "page_size": page_size,
    }
    if query is not None:
        values["query"] = _parse_component(query, "query", _QUERY_ADAPTER, list)
    if filter is not None:
        raw_filter = _decode_component(filter, "filter", dict)
        _validate_filter_operators(raw_filter)
        values["filter"] = _validate_component(
            raw_filter,
            "filter",
            _FILTER_ADAPTER,
        )
        _validate_between_values(values["filter"])
    if sorter is not None:
        raw_sorter = _decode_component(sorter, "sorter", list)
        _validate_sort_directions(raw_sorter)
        values["sorter"] = _validate_component(
            raw_sorter,
            "sorter",
            _SORTER_ADAPTER,
        )
    try:
        return model.model_validate(values)
    except ListQueryError:
        raise
    except ValidationError as error:
        raise ListQueryError(
            "Invalid list request",
            code="LIST_QUERY_INVALID",
            details={"component": "request", "errors": error.errors(include_url=False)},
        ) from error


def _parse_component(raw: str, name: str, adapter: TypeAdapter, shape: type):
    value = _decode_component(raw, name, shape)
    if name == "query":
        _validate_query_words(value)
    return _validate_component(value, name, adapter)


def _decode_component(raw: str, name: str, shape: type):
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ListQueryError(
            f"Invalid {name} JSON",
            code=f"LIST_{name.upper()}_INVALID",
            details={
                "component": name,
                "path": "$",
                "position": error.pos,
            },
        ) from error
    if not isinstance(value, shape):
        raise ListQueryError(
            f"Invalid {name} expression",
            code=f"LIST_{name.upper()}_INVALID",
            details={
                "component": name,
                "path": "$",
                "expected": shape.__name__,
            },
        )
    return value


def _validate_component(value: Any, name: str, adapter: TypeAdapter):
    try:
        return adapter.validate_python(value)
    except ValidationError as error:
        raise ListQueryError(
            f"Invalid {name} expression",
            code=f"LIST_{name.upper()}_INVALID",
            details={
                "component": name,
                "errors": error.errors(include_url=False),
            },
        ) from error


def _validate_query_words(value: list[Any]) -> None:
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        word = item.get("word")
        if not isinstance(word, str) or not word.strip() or len(word) > 200:
            raise ListQueryError(
                "Invalid query word",
                code="LIST_QUERY_WORD_INVALID",
                details={"component": "query", "path": f"[{index}].word"},
            )


def _validate_filter_operators(value: dict[str, Any], path: str = "$") -> None:
    operator = value.get("op")
    if "key" in value:
        if operator not in COMPARISON_OPERATORS:
            raise ListQueryError(
                "Filter operator is not supported",
                code="LIST_FILTER_OPERATOR_NOT_SUPPORTED",
                details={
                    "component": "filter",
                    "path": f"{path}.op",
                    "operator": operator,
                    "supported": list(COMPARISON_OPERATORS),
                },
            )
        return
    if operator not in LOGICAL_OPERATORS:
        raise ListQueryError(
            "Filter operator is not supported",
            code="LIST_FILTER_OPERATOR_NOT_SUPPORTED",
            details={
                "component": "filter",
                "path": f"{path}.op",
                "operator": operator,
                "supported": list(LOGICAL_OPERATORS),
            },
        )
    expressions = value.get("expression")
    if not isinstance(expressions, list):
        return
    for index, expression in enumerate(expressions):
        if isinstance(expression, dict):
            _validate_filter_operators(expression, f"{path}.expression[{index}]")


def _validate_sort_directions(value: list[Any]) -> None:
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        direction = item.get("direction")
        if direction not in SORT_DIRECTIONS:
            raise ListQueryError(
                "Sorter direction is not supported",
                code="LIST_SORTER_DIRECTION_NOT_SUPPORTED",
                details={
                    "component": "sorter",
                    "path": f"[{index}].direction",
                    "direction": direction,
                    "supported": list(SORT_DIRECTIONS),
                },
            )


def _validate_between_values(expression: FilterExpression) -> None:
    if isinstance(expression, FilterFieldExpression):
        if expression.op != "between":
            return
        try:
            BetweenValue.model_validate(expression.val)
        except ValidationError as error:
            raise ListQueryError(
                "Invalid between value",
                code="LIST_FILTER_VALUE_INVALID",
                details={
                    "component": "filter",
                    "key": expression.key,
                    "errors": error.errors(include_url=False),
                },
            ) from error
        return
    for child in expression.expression:
        _validate_between_values(child)


def _filter_metrics(expression: FilterExpression) -> tuple[int, int]:
    if isinstance(expression, FilterFieldExpression):
        return 1, 1
    child_metrics = [_filter_metrics(child) for child in expression.expression]
    return (
        1 + max(depth for depth, _ in child_metrics),
        sum(fields for _, fields in child_metrics),
    )


class ListSorter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    order: Literal["asc", "desc"] = "desc"


FilterModel = TypeVar("FilterModel", bound=BaseModel)
SorterModel = TypeVar("SorterModel", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ListQuery:
    page: int
    page_size: int
    q: str
    filter: BaseModel
    sorter: ListSorter


def parse_query_object(
    raw: str,
    model: type[FilterModel] | type[SorterModel],
    name: str,
) -> FilterModel | SorterModel:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as error:
        raise ListQueryError(
            f"{name} must be a JSON object",
            details={"parameter": name, "position": error.pos},
        ) from error
    if not isinstance(value, dict):
        raise ListQueryError(
            f"{name} must be a JSON object",
            details={"parameter": name},
        )
    try:
        return model.model_validate(value)
    except ValidationError as error:
        raise ListQueryError(
            f"invalid {name}",
            details={
                "parameter": name,
                "errors": error.errors(include_url=False),
            },
        ) from error
