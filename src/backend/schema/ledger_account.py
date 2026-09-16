from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LedgerAccountUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_code: str = Field(min_length=1, max_length=120)
    expected_projection_version: int = Field(ge=1)


class LedgerAccountRead(BaseModel):
    ledger_id: int
    account_code: str
    projection_version: int
    updated_time: datetime


class LedgerAccountResponse(BaseModel):
    status: Literal[200] = 200
    message: str = "ok"
    body: LedgerAccountRead
