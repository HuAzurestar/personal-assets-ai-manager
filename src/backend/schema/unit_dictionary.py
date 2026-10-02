"""Finite, code-owned unit metadata; not a database collection."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schema.response import SuccessResponse


class UnitDefinitionPO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    label: str
    dimension: Literal["CURRENCY", "MASS", "COUNT"]
    quantum: str
    precision: int = Field(strict=True, ge=0, le=8)
    is_default: bool


class UnitDictionaryPO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[UnitDefinitionPO] = Field(min_length=1, max_length=128)


class UnitDictionaryResponse(SuccessResponse[UnitDictionaryPO]):
    pass
