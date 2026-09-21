from __future__ import annotations

from sqlalchemy import Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable


class ReviewRevision(TargetTable, TargetBase):
    __tablename__ = "review_revision"
    __table_args__ = (
        Index("ix_review_revision_case_id", "review_case_id", "id"),
        Index(
            "uq_review_revision_idempotency_key",
            "idempotency_key",
            unique=True,
            sqlite_where=text("idempotency_key <> ''"),
        ),
    )

    review_case_id: Mapped[int] = mapped_column(Integer, nullable=False)
    operation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    actor: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False, default="")
