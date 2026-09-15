from __future__ import annotations


from sqlalchemy import BigInteger, Index, Integer, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase

from backend.entity.base import TargetTable


class ReviewCaseBill(TargetTable, TargetBase):
    __tablename__ = "review_case_bill"
    __table_args__ = (
        Index("ix_review_case_bill_bill_case", "bill_id", "case_id"),
        Index("ix_review_case_bill_case_id", "case_id", "id"),
        Index("ix_review_case_bill_economic_id", "economic_id", "id"),
    )

    case_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bill_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    economic_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    entry_type: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    role: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    party: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    amount_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    amount_scale: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=2)
    currency_code: Mapped[str] = mapped_column(String(12), nullable=False, default="CNY")
