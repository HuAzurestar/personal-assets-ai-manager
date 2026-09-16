from __future__ import annotations

from sqlalchemy import Integer, String, case, literal
from sqlalchemy.orm import Mapped, column_property, mapped_column
from sqlalchemy.types import TypeDecorator

from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable


class ReviewStatus(TypeDecorator):
    impl = Integer
    cache_ok = True

    values = {"CONFIRMED": 0, "REVOKED": 1}
    names = {value: key for key, value in values.items()}

    def process_bind_param(self, value, _dialect):
        return self.values.get(value, value)

    def process_result_value(self, value, _dialect):
        return self.names.get(value, value)


class ReviewCase(TargetTable, TargetBase):
    __tablename__ = "review_case"

    behavior_type: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(ReviewStatus(), nullable=False, default="CONFIRMED")
    title: Mapped[str] = mapped_column(String(160), nullable=False, default="")

    # Temporary query aliases for read paths while support features leave Review.
    review_type = column_property(literal("LEDGER"))
    behavior_code = column_property(case(
        (behavior_type == 1, "BORROW_AND_REPAY"),
        else_="NORMAL_TRANSACTION",
    ))
    allocation_status = column_property(literal("COMPLETE"))
    version = column_property(literal(1))
    result_json = column_property(literal("{}"))
