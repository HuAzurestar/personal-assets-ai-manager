"""Persist exact protected model messages and bounded raw returns in SQLite.

The trace table is deliberately write-only from the application. Its content is
sensitive even after input sanitization and must not enter HTTP or ordinary logs.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from backend.error import LlmAdapterError
from backend.mapper.llm_prompt_audit_mapper import LlmPromptAuditMapper

MAX_CAPTURED_RESPONSE_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class PromptAuditContext:
    run_id: str
    rule_id: int
    rule_revision: int
    ledger_id: int
    model_id: int
    attempt: int


class LlmPromptAuditService:
    def __init__(self, sessions: Callable[[], Session]):
        self._sessions = sessions

    def start(self, context: PromptAuditContext, *, model_name: str, request: dict) -> int:
        # The key is passed separately to LiteLLM and is never in this allowlist.
        request_json = json.dumps({
            "messages": request["messages"],
            "response_format": request["response_format"],
        }, ensure_ascii=False, separators=(",", ":"))
        try:
            with self._sessions() as db:
                mapper = LlmPromptAuditMapper(db)
                mapper.begin_write()
                audit_id = mapper.start({
                    "run_id": context.run_id,
                    "rule_id": context.rule_id,
                    "rule_revision": context.rule_revision,
                    "ledger_id": context.ledger_id,
                    "model_id": context.model_id,
                    "attempt": context.attempt,
                    "model_name": model_name,
                    "request_json": request_json,
                })
                db.commit()
                return audit_id
        except Exception:
            # Do not call the provider if the outbound prompt cannot be recorded.
            raise LlmAdapterError(
                "The model prompt audit could not be saved", code="AUDIT_STORAGE_ERROR",
            ) from None

    def finish(
        self, audit_id: int, *, status: str, response_text: str = "",
        error_code: str = "",
    ) -> None:
        encoded = response_text.encode("utf-8")
        truncated = len(encoded) > MAX_CAPTURED_RESPONSE_BYTES
        if truncated:
            response_text = encoded[:MAX_CAPTURED_RESPONSE_BYTES].decode("utf-8", errors="ignore")
        try:
            with self._sessions() as db:
                mapper = LlmPromptAuditMapper(db)
                mapper.begin_write()
                mapper.finish(
                    audit_id, status=status, response_text=response_text,
                    response_truncated=truncated, error_code=error_code,
                )
                db.commit()
        except Exception:
            raise LlmAdapterError(
                "The model response audit could not be saved", code="AUDIT_STORAGE_ERROR",
            ) from None
