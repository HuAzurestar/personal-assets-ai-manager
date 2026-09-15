from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class DefaultProjectionFactVO:
    id: int
    fact_key: str
    occurred_time: datetime
    cash_direction: str
    amount_value: int
    amount_scale: int
    currency_code: str
    account_code: str
    counterparty: str
    summary: str
    created_time: datetime
    updated_time: datetime


@dataclass(frozen=True, slots=True)
class FactNatureEvidenceVO:
    bill_id: int
    source_type: str
    nature: str
    raw_hash: str


@dataclass(frozen=True, slots=True)
class DefaultProjectionWriteVO:
    fact_id: int
    ledger_type: str
    allocation_status: str
    title: str
    start_time: datetime
    end_time: datetime
    in_amount_value: int
    in_amount_scale: int
    in_currency_code: str
    out_amount_value: int
    out_amount_scale: int
    out_currency_code: str
    in_account_code: str
    out_account_code: str
    input_hash: str
    created_time: datetime
    updated_time: datetime


@dataclass(frozen=True, slots=True)
class FinancialProjectionWriteVO:
    fact_ids: tuple[int, ...]
    case_id: int
    ledger_type: str
    allocation_status: str
    title: str
    start_time: datetime
    end_time: datetime
    in_amount_value: int
    in_amount_scale: int
    in_currency_code: str
    out_amount_value: int
    out_amount_scale: int
    out_currency_code: str
    in_account_code: str
    out_account_code: str
    input_hash: str
    created_time: datetime
    updated_time: datetime
