"""Fixed fictional-input preview; no arbitrary Ledger or text input is accepted."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.core.money import normalize_currency_code
from backend.schema.response import SuccessResponse


class DisclosurePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    sample: Literal["MEAL_SMALL", "MEAL_LARGE", "NON_MEAL_SMALL", "NO_CONTEXT"] = "MEAL_SMALL"
    amount_mode: Literal[1, 2, 3] = 1
    currency_code: str = Field(default="CNY", max_length=16)

    @field_validator("amount_mode", mode="before")
    @classmethod
    def validate_mode(cls, value: object) -> int:
        if type(value) is not int:
            raise ValueError("amount_mode must be an integer")
        return value

    @field_validator("currency_code")
    @classmethod
    def validate_currency(cls, value: str) -> str:
        if normalize_currency_code(value) != value:
            raise ValueError("use a canonical currency unit")
        return value


class DisclosurePreviewSample(BaseModel):
    label: str
    amount: int
    currency_code: str
    merchant: str
    summary: str


class DisclosurePreviewMessage(BaseModel):
    role: Literal["system", "user"]
    content: str


class DisclosurePreviewRead(BaseModel):
    mode: Literal["SYNTHETIC_PREVIEW"] = "SYNTHETIC_PREVIEW"
    policy_source: Literal["SAVED_SETTING"] = "SAVED_SETTING"
    model_called: Literal[False] = False
    sample_id: str
    sample: DisclosurePreviewSample
    input_eligible: bool
    messages: list[DisclosurePreviewMessage]
    warnings: list[str]


class DisclosurePreviewResponse(SuccessResponse[DisclosurePreviewRead]):
    body: DisclosurePreviewRead
