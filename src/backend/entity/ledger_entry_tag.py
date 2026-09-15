from __future__ import annotations


from sqlalchemy import Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase

from backend.entity.base import TargetTable


class LedgerEntryTag(TargetTable, TargetBase):
    __tablename__ = "ledger_entry_tag"
    __table_args__ = (
        UniqueConstraint("ledger_id", "tag_id", name="uq_ledger_entry_tag"),
    )

    ledger_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tag_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
