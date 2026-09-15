from __future__ import annotations


from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase

from backend.entity.base import TargetTable


class ReviewCase(TargetTable, TargetBase):
    __tablename__ = "review_case"

    review_type: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    behavior_code: Mapped[str] = mapped_column(String(40), nullable=False, default="UNKNOWN")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    allocation_status: Mapped[str] = mapped_column(String(20), nullable=False, default="PARTIAL")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    result_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
