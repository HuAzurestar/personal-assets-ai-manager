from __future__ import annotations

from datetime import datetime
from sqlalchemy import BigInteger, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable, UTCISO8601DateTime


class ReviewLedgerPositionLegAllocation(TargetTable, TargetBase):
    __tablename__ = "review_ledger_position_leg_allocation"
    __table_args__ = (
        UniqueConstraint("ledger_id", "position_leg_id"),
        Index("ix_position_allocation_review", "review_id", "id"),
        Index("ix_position_allocation_leg", "position_leg_id", "id"),
    )
    review_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ledger_id: Mapped[int] = mapped_column(Integer, nullable=False)
    position_leg_id: Mapped[int] = mapped_column(Integer, nullable=False)
    cash_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cash_currency_code: Mapped[str] = mapped_column(String(12), nullable=False)
