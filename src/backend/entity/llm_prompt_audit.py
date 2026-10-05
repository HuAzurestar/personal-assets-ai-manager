from __future__ import annotations

from sqlalchemy import CheckConstraint, Integer, Text, Index
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable


class LlmPromptAudit(TargetTable, TargetBase):
    __tablename__ = "llm_prompt_audit"
    __table_args__ = (
        CheckConstraint("rule_id > 0", name="ck_llm_prompt_audit_rule_id"),
        CheckConstraint("rule_revision > 0", name="ck_llm_prompt_audit_rule_revision"),
        CheckConstraint("ledger_id > 0", name="ck_llm_prompt_audit_ledger_id"),
        CheckConstraint("model_id > 0", name="ck_llm_prompt_audit_model_id"),
        CheckConstraint("attempt BETWEEN 1 AND 3", name="ck_llm_prompt_audit_attempt"),
        CheckConstraint("response_truncated IN (0, 1)", name="ck_llm_prompt_audit_truncated"),
        CheckConstraint(
            "status IN ('STARTED', 'SUCCEEDED', 'SUGGESTED', 'INSUFFICIENT', 'REJECTED', 'ERROR')",
            name="ck_llm_prompt_audit_status",
        ),
    )

    run_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    rule_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rule_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ledger_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    response_truncated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source: Mapped[str] = mapped_column(Text, nullable=False, default="legacy.tag-scan")
    operation_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    prompt_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost: Mapped[str | None] = mapped_column(Text, nullable=True)


Index("ix_llm_prompt_audit_item", LlmPromptAudit.rule_id, LlmPromptAudit.ledger_id)
