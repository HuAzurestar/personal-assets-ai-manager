from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase

from backend.entity.base import TargetTable


class LedgerEntry(TargetTable, TargetBase):
    __tablename__ = "ledger_entry"
    __table_args__ = (Index("ix_ledger_entry_occurred_time_id", "occurred_time", "id"),)

    entry_type: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_direction: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_scale: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)
    currency_code: Mapped[str] = mapped_column(String(12), nullable=False)
    account_code: Mapped[str] = mapped_column(String(120), nullable=False)
    counterparty_account_ref: Mapped[str] = mapped_column(
        String(200), nullable=False, default=""
    )
    occurred_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), nullable=False
    )
