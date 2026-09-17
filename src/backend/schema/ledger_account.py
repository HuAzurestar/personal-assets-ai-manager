from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from backend.schema.response import SuccessResponse


class LedgerAccountUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_code: str = Field(min_length=1, max_length=120)
    expected_projection_version: int = Field(ge=1)


class LedgerAccountRead(BaseModel):
    ledger_id: int
    account_code: str
    projection_version: int
    updated_time: datetime


class LedgerAccountResponse(SuccessResponse[LedgerAccountRead]):
    body: LedgerAccountRead
