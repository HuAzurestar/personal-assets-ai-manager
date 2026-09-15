from __future__ import annotations


from sqlalchemy import Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase

from backend.entity.base import TargetTable


class LedgerEntrySource(TargetTable, TargetBase):
    __tablename__ = "ledger_entry_source"
    __table_args__ = (
        UniqueConstraint("source_kind", "source_id", name="uq_ledger_entry_source"),
        Index(
            "ix_ledger_entry_source_ledger_kind_id",
            "ledger_id",
            "source_kind",
            "source_id",
        ),
    )

    ledger_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False, default="BILL_FACT")
    source_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
