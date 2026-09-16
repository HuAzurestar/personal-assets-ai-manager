from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from backend.error import ListQueryError


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
