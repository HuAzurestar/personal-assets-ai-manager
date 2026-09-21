from __future__ import annotations


from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase

from backend.entity.base import TargetTable


class TargetTagView(TargetTable, TargetBase):
    __tablename__ = "tag_view"
    __table_args__ = (UniqueConstraint("system_name", name="uq_target_tag_view_system_name"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    system_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
