from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.money import cents


Role = Literal[
    "expense",
    "income",
    "receivable",
    "payable",
    "repayment_received",
    "repayment_paid",
    "transfer",
]


class MatterLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bill_id: int = Field(gt=0)
    amount: float = Field(gt=0)
    role: Role
    party: str = Field(default="", max_length=120)

    @field_validator("amount")
    @classmethod
    def exact_amount(cls, value):
        cents(value)
        return value


class MatterWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    scenarios: list[str] = Field(default_factory=list, max_length=12)
    lines: list[MatterLine] = Field(min_length=1, max_length=100)
    own_accounts_confirmed: bool = False
    expected_version: int = Field(default=0, ge=0)
    reason: str = Field(default="", max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=120)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value):
        if not value.strip():
            raise ValueError("请填写事项名称")
        return value.strip()

    @field_validator("scenarios")
    @classmethod
    def clean_scenarios(cls, values):
        if any(not value.strip() or len(value) > 40 for value in values):
            raise ValueError("场景标签需为 1–40 个字符")
        return list(dict.fromkeys(value.strip() for value in values))


class MatterUndo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    reason: str = Field(default="", max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=120)


class MatterLineRead(BaseModel):
    bill_id: int
    amount_cents: int
    amount: float
    role: Role
    party: str
    merchant: str
    account_name: str
    occurred_at: datetime
    bill_amount: float


class MatterBalanceRead(BaseModel):
    kind: str
    party: str
    amount_cents: int


class MatterHistoryRead(BaseModel):
    version: int
    action: str
    actor: str
    reason: str
    created_at: datetime
    snapshot: dict[str, object]


class MatterRead(BaseModel):
    title: str
    scenarios: list[str]
    lines: list[MatterLineRead]
    own_accounts_confirmed: bool
    balances: list[MatterBalanceRead]
    id: int
    version: int
    status: str
    history: list[MatterHistoryRead]


class MatterSummaryRead(BaseModel):
    id: int
    version: int
    title: str
    scenarios: list[str]
    own_accounts_confirmed: bool
    balances: list[MatterBalanceRead]
    status: str
    line_count: int


class MatterPageRead(BaseModel):
    items: list[MatterSummaryRead]
    total: int
    page: int
    page_size: int


@dataclass(frozen=True, slots=True)
class MatterVO:
    id: int
    version: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MatterRevisionVO:
    id: int
    matter_id: int
    version: int
    action: str
    snapshot: str
    reason: str
    actor: str
    idempotency_key: str
    request_payload: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CurrentMatterVO:
    matter_id: int
    version: int
    action: str
    snapshot: str


@dataclass(frozen=True, slots=True)
class MatterSummaryVO:
    matter_id: int
    version: int
    action: str
    title: str
    scenarios: str
    own_accounts_confirmed: bool
    balances: str
    line_count: int


@dataclass(frozen=True, slots=True)
class MatterBillVO:
    id: int
    occurred_at: datetime
    merchant: str
    amount: float
    account_name: str
    aggregate_excluded: bool
