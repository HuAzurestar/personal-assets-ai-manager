from __future__ import annotations

from datetime import datetime
from sqlalchemy import BigInteger, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable, UTCISO8601DateTime


class LedgerAccount(TargetTable, TargetBase):
    __tablename__ = "ledger_account"
    __table_args__ = (Index("ledger_account_party_lookup", "party_id", "id"),)
    party_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    statement_interval_months: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snapshot_interval_months: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
