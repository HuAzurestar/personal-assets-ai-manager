from sqlalchemy import CheckConstraint, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable


class AiInvocation(TargetTable, TargetBase):
    __tablename__ = "ai_invocation"
    __table_args__ = (
        CheckConstraint("status IN ('STARTED', 'SUCCEEDED', 'REJECTED', 'ERROR')"),
        CheckConstraint("task_version > 0 AND prompt_version > 0 AND model_id > 0 AND attempt > 0"),
        CheckConstraint("input_tokens >= -1 AND output_tokens >= -1 AND total_tokens >= -1 AND cached_tokens >= -1"),
        CheckConstraint("latency_ms >= 0"),
        CheckConstraint("response_truncated IN (0, 1)"),
    )

    task_key: Mapped[str] = mapped_column(Text, nullable=False)
    task_version: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_key: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    model_id: Mapped[int] = mapped_column(Integer, nullable=False)
    model_name: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    result_code: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_code: Mapped[str] = mapped_column(Text, nullable=False, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=-1)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=-1)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=-1)
    cached_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=-1)
    cost_usd: Mapped[str] = mapped_column(Text, nullable=False, default="")
    request_json: Mapped[str] = mapped_column(Text, nullable=False)
    response_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    response_truncated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
