from __future__ import annotations

from datetime import datetime
from sqlalchemy import BigInteger, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable, UTCISO8601DateTime


class LedgerAccountParty(TargetTable, TargetBase):
    __tablename__ = "ledger_account_party"
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
