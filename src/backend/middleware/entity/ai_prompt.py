from sqlalchemy import CheckConstraint, Index, Integer, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from backend.core.target_database import TargetBase
from backend.entity.base import TargetTable


class AiPrompt(TargetTable, TargetBase):
    __tablename__ = "ai_prompt"
    __table_args__ = (
        UniqueConstraint("prompt_key", "version"),
        CheckConstraint("version > 0"),
        CheckConstraint("state IN ('DRAFT', 'PRODUCTION', 'RETIRED')"),
        Index("uq_ai_prompt_production", "prompt_key", unique=True,
              sqlite_where=text("state = 'PRODUCTION'")),
    )

    prompt_key: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    state: Mapped[str] = mapped_column(Text, nullable=False)
