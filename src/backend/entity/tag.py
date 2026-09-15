from __future__ import annotations


from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase

from backend.entity.base import TargetTable


class TargetTag(TargetTable, TargetBase):
    __tablename__ = "tag"
    __table_args__ = (
        UniqueConstraint("view_id", "system_name", name="uq_target_tag_system_name"),
    )

    view_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    system_name: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
