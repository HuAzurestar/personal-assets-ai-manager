from __future__ import annotations

from datetime import datetime
from sqlalchemy import BigInteger, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable, UTCISO8601DateTime


class Position(TargetTable, TargetBase):
    __tablename__ = "position"
    __table_args__ = (Index("ix_position_party_type_usage", "party_id", "type", "usage_scenario", "status", "id"),)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    usage_scenario: Mapped[str] = mapped_column(String(40), nullable=False)
    party_id: Mapped[int] = mapped_column(Integer, nullable=False)
    counterparty: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    unit_code: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
