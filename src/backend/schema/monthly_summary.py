"""Independent, fictional-only extension proof; no ledger/tag DTO reuse."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class MonthlyCategory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    name: str = Field(min_length=1, max_length=64)
    count: int = Field(ge=0, le=100000)


class DisclosedMonthlySummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    synthetic: Literal[True]
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    entry_count: int = Field(ge=0, le=100000)
    categories: tuple[MonthlyCategory, ...] = Field(max_length=32)


class MonthlySummaryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    summary: str = Field(min_length=1, max_length=2000)
    highlights: list[str] = Field(max_length=5)

