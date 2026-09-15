from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase

from backend.entity.base import TargetTable


class BillFact(TargetTable, TargetBase):
    __tablename__ = "bill_fact"
    __table_args__ = (
        UniqueConstraint("fact_key", name="uq_bill_fact_key"),
        Index("ix_bill_fact_occurred_time_id", "occurred_time", "id"),
    )

    fact_key: Mapped[str] = mapped_column(String(160), nullable=False)
    occurred_time: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    cash_direction: Mapped[str] = mapped_column(String(8), nullable=False)
    amount_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_scale: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)
    currency_code: Mapped[str] = mapped_column(String(12), nullable=False, default="CNY")
    account_code: Mapped[str] = mapped_column(String(120), nullable=False, default="UNKNOWN")
    counterparty: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
