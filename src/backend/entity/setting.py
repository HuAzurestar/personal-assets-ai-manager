from __future__ import annotations

from sqlalchemy import CheckConstraint, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable


class Setting(TargetTable, TargetBase):
    __tablename__ = "setting"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_setting_singleton_id"),
    )

    value_json: Mapped[str] = mapped_column(Text, nullable=False)
