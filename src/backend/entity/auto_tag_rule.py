from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable


AUTO_TAG_METHOD_LLM_DIRECT = 1
AMOUNT_MODE_BAND = 1
AMOUNT_MODE_EXACT = 2
AMOUNT_MODE_NONE = 3
MAX_COUNTER_VALUE = 9_223_372_036_854_775_807


class AutoTagRule(TargetTable, TargetBase):
    __tablename__ = "auto_tag_rule"
    last_analysis_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}", server_default="{}")
    __table_args__ = (
        CheckConstraint("view_id > 0", name="ck_auto_tag_rule_view_id"),
        CheckConstraint("method = 1", name="ck_auto_tag_rule_method"),
        CheckConstraint("enabled IN (0, 1)", name="ck_auto_tag_rule_enabled"),
        CheckConstraint(
            "amount_mode IN (1, 2, 3)",
            name="ck_auto_tag_rule_amount_mode",
        ),
        CheckConstraint(
            "rule_revision >= 1",
            name="ck_auto_tag_rule_rule_revision",
        ),
        CheckConstraint(
            "scan_after_ledger_id >= 0",
            name="ck_auto_tag_rule_scan_after_ledger_id",
        ),
        CheckConstraint("scan_epoch >= 1", name="ck_auto_tag_rule_scan_epoch"),
        CheckConstraint(
            f"analyzed_count BETWEEN 0 AND {MAX_COUNTER_VALUE}",
            name="ck_auto_tag_rule_analyzed_count",
        ),
        CheckConstraint(
            f"failed_count BETWEEN 0 AND {MAX_COUNTER_VALUE}",
            name="ck_auto_tag_rule_failed_count",
        ),
        CheckConstraint(
            f"suggested_count BETWEEN 0 AND {MAX_COUNTER_VALUE}",
            name="ck_auto_tag_rule_suggested_count",
        ),
        CheckConstraint(
            f"accepted_count BETWEEN 0 AND {MAX_COUNTER_VALUE}",
            name="ck_auto_tag_rule_accepted_count",
        ),
        CheckConstraint(
            f"rejected_count BETWEEN 0 AND {MAX_COUNTER_VALUE}",
            name="ck_auto_tag_rule_rejected_count",
        ),
        Index("ix_auto_tag_rule_view_id", "view_id"),
    )

    name: Mapped[str] = mapped_column(Text, nullable=False)
    view_id: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=AUTO_TAG_METHOD_LLM_DIRECT,
    )
    method_config_json: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cron: Mapped[str] = mapped_column(Text, nullable=False, default="")
    amount_mode: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=AMOUNT_MODE_BAND,
    )
    rule_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    scan_after_ledger_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    scan_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    analyzed_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    suggested_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    accepted_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    rejected_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
