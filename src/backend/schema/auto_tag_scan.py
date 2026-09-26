"""Synthetic-only contracts for the M1 automatic-tag scan loop."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.schema.llm_analysis import LlmAmountDisclosure


class SyntheticTagScanFixture(BaseModel):
    """Auditable fixture data; production Ledger fields cannot enter this DTO."""

    model_config = ConfigDict(extra="forbid", strict=True)

    source: Literal["SYNTHETIC_FIXTURE"]
    ledger_id: int = Field(ge=1)
    item: str = Field(min_length=1, max_length=120)
    direction: Literal["IN", "OUT"]
    merchant: str = Field(default="", max_length=200)
    summary: str = Field(default="", max_length=500)
    amount: LlmAmountDisclosure
