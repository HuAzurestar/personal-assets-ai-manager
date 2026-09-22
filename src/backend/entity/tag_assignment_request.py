from __future__ import annotations

from sqlalchemy import CheckConstraint, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable


TAG_REQUEST_STATUS_PENDING = 1
TAG_REQUEST_STATUS_ENABLED = 2
TAG_REQUEST_STATUS_REJECTED = 3
TAG_REQUEST_STATUS_CANCELLED = 4
TAG_REQUEST_STATUS_REPLACED = 5


class TagAssignmentRequest(TargetTable, TargetBase):
    __tablename__ = "tag_assignment_request"
    __table_args__ = (
        CheckConstraint("rule_id > 0", name="ck_tag_assignment_request_rule_id"),
        CheckConstraint(
            "rule_revision > 0",
            name="ck_tag_assignment_request_rule_revision",
        ),
        CheckConstraint(
            "ledger_id > 0",
            name="ck_tag_assignment_request_ledger_id",
        ),
        CheckConstraint("view_id > 0", name="ck_tag_assignment_request_view_id"),
        CheckConstraint(
            "proposed_tag_id > 0",
            name="ck_tag_assignment_request_proposed_tag_id",
        ),
        CheckConstraint(
            "status IN (1, 2, 3, 4, 5)",
            name="ck_tag_assignment_request_status",
        ),
        CheckConstraint(
            "length(reason_summary) <= 200",
            name="ck_tag_assignment_request_reason_summary",
        ),
    )

    rule_id: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    ledger_id: Mapped[int] = mapped_column(Integer, nullable=False)
    view_id: Mapped[int] = mapped_column(Integer, nullable=False)
    proposed_tag_id: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=TAG_REQUEST_STATUS_PENDING,
    )
    reason_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
