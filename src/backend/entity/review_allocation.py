from __future__ import annotations

from sqlalchemy import BigInteger, Index, Integer, SmallInteger, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable


class ReviewAllocation(TargetTable, TargetBase):
    __tablename__ = "review_allocation"
    __table_args__ = (
        UniqueConstraint("ledger_entry_id", name="uq_review_allocation_ledger_entry_id"),
        Index("ix_review_allocation_case_id", "review_case_id", "id"),
        Index(
            "ix_review_allocation_fact_case",
            "transaction_fact_id",
            "review_case_id",
            "id",
        ),
    )

    review_case_id: Mapped[int] = mapped_column(Integer, nullable=False)
    transaction_fact_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ledger_entry_id: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_scale: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)
    currency_code: Mapped[str] = mapped_column(String(12), nullable=False)
