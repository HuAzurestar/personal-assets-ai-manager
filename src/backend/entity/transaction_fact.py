from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable, UTCISO8601DateTime


CASH_DIRECTION_IN = 1
CASH_DIRECTION_OUT = 2


class TransactionFact(TargetTable, TargetBase):
    __tablename__ = "transaction_fact"
    __table_args__ = (
        UniqueConstraint("fact_key", name="uq_transaction_fact_key"),
        CheckConstraint(
            f"cash_direction IN ({CASH_DIRECTION_IN}, {CASH_DIRECTION_OUT})",
            name="ck_transaction_fact_cash_direction",
        ),
        CheckConstraint("amount > 0", name="ck_transaction_fact_amount"),
        Index(
            "ix_transaction_fact_occurred_time_id",
            "occurred_time",
            "id",
        ),
    )

    fact_key: Mapped[str] = mapped_column(String(160), nullable=False)
    occurred_time: Mapped[datetime] = mapped_column(
        UTCISO8601DateTime(), nullable=False
    )
    cash_direction: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(12), nullable=False)
    account_code: Mapped[str] = mapped_column(String(120), nullable=False)
    counterparty_name: Mapped[str] = mapped_column(
        String(200), nullable=False, default=""
    )
    counterparty_account_ref: Mapped[str] = mapped_column(
        String(200), nullable=False, default=""
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
