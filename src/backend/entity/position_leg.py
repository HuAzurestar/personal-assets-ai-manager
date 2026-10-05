from __future__ import annotations

from datetime import datetime
from sqlalchemy import BigInteger, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable, UTCISO8601DateTime


class PositionLeg(TargetTable, TargetBase):
    __tablename__ = "position_leg"
    __table_args__ = (
        Index("ix_position_leg_position_time", "position_id", "occurred_time", "id"),
        Index("ix_position_leg_review", "review_id", "id"),
        Index("ix_position_leg_source", "source_position_leg_id", "review_id", "id"),
    )
    position_id: Mapped[int] = mapped_column(Integer, nullable=False)
    review_id: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    leg_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    leg_direction: Mapped[str] = mapped_column(String(4), nullable=False)
    occurred_time: Mapped[datetime] = mapped_column(UTCISO8601DateTime(), nullable=False)
    source_position_leg_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    basis: Mapped[str] = mapped_column(Text, nullable=False, default="")
