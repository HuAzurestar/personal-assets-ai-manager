from __future__ import annotations

from sqlalchemy import BigInteger, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, synonym

from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable


class ReviewAllocation(TargetTable, TargetBase):
    __tablename__ = "review_transaction_ledger_allocation"
    __table_args__ = (
        UniqueConstraint("ledger_id", name="uq_review_allocation_ledger_entry_id"),
        Index("ix_review_allocation_case_id", "review_id", "id"),
        Index(
            "ix_review_allocation_fact_case",
            "transaction_id",
            "review_id",
            "id",
        ),
        Index("ix_default_review_lookup", "transaction_id", "review_id"),
    )

    review_case_id: Mapped[int] = mapped_column("review_id", Integer, nullable=False)
    transaction_fact_id: Mapped[int] = mapped_column("transaction_id", Integer, nullable=False)
    ledger_entry_id: Mapped[int] = mapped_column("ledger_id", Integer, nullable=False)
    amount: Mapped[int] = mapped_column("cash_amount", BigInteger, nullable=False)
    currency_code: Mapped[str] = mapped_column("cash_currency_code", String(12), nullable=False)
    review_id = synonym("review_case_id")
    transaction_id = synonym("transaction_fact_id")
    ledger_id = synonym("ledger_entry_id")
    cash_amount = synonym("amount")
    cash_currency_code = synonym("currency_code")
