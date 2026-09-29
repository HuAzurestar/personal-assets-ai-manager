from __future__ import annotations

from sqlalchemy import insert, text, update
from sqlalchemy.orm import Session

from backend.entity import LlmPromptAudit
from backend.entity.base import utc_now


class LlmPromptAuditMapper:
    """Write-only persistence for the independent prompt trace table."""

    def __init__(self, db: Session):
        self.db = db

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    def start(self, values: dict[str, object]) -> int:
        now = utc_now()
        result = self.db.execute(insert(LlmPromptAudit).values(
            **values, status="STARTED", response_text="", response_truncated=0,
            error_code="", created_time=now, updated_time=now,
        ))
        return int(result.inserted_primary_key[0])

    def finish(
        self, audit_id: int, *, status: str, response_text: str,
        response_truncated: bool, error_code: str,
    ) -> None:
        result = self.db.execute(update(LlmPromptAudit).where(
            LlmPromptAudit.id == audit_id,
            LlmPromptAudit.status == "STARTED",
        ).values(
            status=status,
            response_text=response_text,
            response_truncated=int(response_truncated),
            error_code=error_code,
            updated_time=utc_now(),
        ))
        if result.rowcount != 1:
            raise RuntimeError("Prompt audit attempt is not open")
