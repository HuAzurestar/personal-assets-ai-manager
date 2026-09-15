from __future__ import annotations


from sqlalchemy import Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase

from backend.entity.base import TargetTable


class ReviewHistory(TargetTable, TargetBase):
    __tablename__ = "review_history"
    __table_args__ = (
        UniqueConstraint("case_id", "version", name="uq_review_history_case_version"),
        Index(
            "uq_review_history_idempotency",
            "idempotency_key",
            unique=True,
            sqlite_where=text("idempotency_key <> ''"),
        ),
    )

    case_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    operation: Mapped[str] = mapped_column(String(20), nullable=False, default="CREATE")
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    request_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    before_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    after_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    reverses_history_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    actor: Mapped[str] = mapped_column(String(120), nullable=False, default="local-user")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False, default="")
