from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase

from backend.entity.base import TargetTable


class LedgerEntry(TargetTable, TargetBase):
    __tablename__ = "ledger_entry"
    __table_args__ = (Index("ix_ledger_entry_time_id", "start_time", "id"),)

    ledger_type: Mapped[str] = mapped_column(String(40), nullable=False, default="UNRESOLVED")
    entry_type: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entry_direction: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    account_code: Mapped[str] = mapped_column(String(120), nullable=False, default="UNKNOWN")
    counterparty_account_ref: Mapped[str] = mapped_column(
        String(200), nullable=False, default=""
    )
    occurred_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=False), nullable=True
    )
    economic_type: Mapped[str] = mapped_column(String(40), nullable=False, default="TRANSACTION")
    cash_direction: Mapped[str] = mapped_column(String(8), nullable=False, default="UNKNOWN")
    amount_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    amount_scale: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)
    currency_code: Mapped[str] = mapped_column(String(12), nullable=False, default="CNY")
    claim_key: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    claim_side: Mapped[str] = mapped_column(String(20), nullable=False, default="UNKNOWN")
    reversal_of_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    allocation_status: Mapped[str] = mapped_column(String(20), nullable=False, default="DEFAULT")
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    in_amount_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    in_amount_scale: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)
    in_currency_code: Mapped[str] = mapped_column(String(12), nullable=False, default="CNY")
    out_amount_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    out_amount_scale: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)
    out_currency_code: Mapped[str] = mapped_column(String(12), nullable=False, default="CNY")
    in_account_code: Mapped[str] = mapped_column(String(120), nullable=False, default="UNKNOWN")
    out_account_code: Mapped[str] = mapped_column(String(120), nullable=False, default="UNKNOWN")
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
