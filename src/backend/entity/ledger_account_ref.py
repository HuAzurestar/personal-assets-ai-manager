from __future__ import annotations

from datetime import datetime
from sqlalchemy import BigInteger, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column
from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable, UTCISO8601DateTime


class LedgerAccountRef(TargetTable, TargetBase):
    __tablename__ = "ledger_account_ref"
    __table_args__ = (
        Index("ledger_account_ref_account", "account_id", "id"),
        Index("ledger_account_ref_source_identity", "source_namespace", "source_identity", unique=True, sqlite_where=text("identity_strength=1")),
    )
    account_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    institution: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    reference: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    source_namespace: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    source_identity: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    identity_strength: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
