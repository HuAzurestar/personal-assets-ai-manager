from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, synonym

from backend.core.target_database import TargetBase

from backend.entity.base import TargetTable, UTCISO8601DateTime


class LedgerEntry(TargetTable, TargetBase):
    __tablename__ = "ledger_entry"
    __table_args__ = (
        Index("ix_ledger_entry_occurred_time_id", "occurred_time", "id"),
        Index("ledger_entry_account_ref_time", "account_ref_id", "cash_currency_code", "occurred_time", "id"),
    )

    entry_type: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_direction: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[int] = mapped_column("cash_amount", BigInteger, nullable=False)
    currency_code: Mapped[str] = mapped_column("cash_currency_code", String(12), nullable=False)
    account_ref_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cash_amount = synonym("amount")
    cash_currency_code = synonym("currency_code")
    account_code: Mapped[str] = mapped_column(String(120), nullable=False)
    counterparty_account_ref: Mapped[str] = mapped_column(
        String(200), nullable=False, default=""
    )
    occurred_time: Mapped[datetime] = mapped_column(
        UTCISO8601DateTime(), nullable=False
    )
