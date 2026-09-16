from __future__ import annotations

from sqlalchemy import Index, Integer, String, Text, literal, text
from sqlalchemy.orm import Mapped, column_property, mapped_column, synonym
from sqlalchemy.types import TypeDecorator

from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable


class RevisionOperation(TypeDecorator):
    impl = Integer
    cache_ok = True

    values = {"CREATE": 0, "UPDATE": 1, "REVOKE": 2, "RESTORE": 3}
    names = {value: key for key, value in values.items()}

    def process_bind_param(self, value, _dialect):
        return self.values.get(value, value)

    def process_result_value(self, value, _dialect):
        return self.names.get(value, value)


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
    operation: Mapped[str] = mapped_column(RevisionOperation(), nullable=False, default="CREATE")
    request_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    after_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    actor: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    idempotency_key: Mapped[str] = mapped_column(String(120), nullable=False, default="")

    case_id = synonym("review_case_id")
    version = column_property(id)
    schema_version = column_property(literal(1))
    snapshot_hash = column_property(literal(""))
    reverses_history_id = column_property(literal(0))
