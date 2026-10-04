"""Auto-tag compatibility adapter over the shared AI runtime."""
from __future__ import annotations

from typing import Protocol
from backend.core import protected_secret_store
from backend.error import LlmAdapterError
from backend.middleware.runtime import AiRuntime, ExecutionContext
from backend.middleware.task import TaskRegistry
from backend.service.auto_tag_task import AUTO_TAG_TASK
from backend.service.llm_adapter import LiteLlmAdapter
from backend.service.llm_prompt_audit_service import LlmPromptAuditService


class ProviderSecretReader(Protocol):
    def get_for_provider(self, model_id: int) -> str | None: ...


provider_secret_reader = protected_secret_store


class ConfiguredLlmAnalyzer:
    def __init__(self, sessions, secret_store, adapter=None, *, runtime=None):
        self._runtime = runtime or AiRuntime(
            sessions, secret_store, TaskRegistry((AUTO_TAG_TASK,)),
            (adapter or LiteLlmAdapter()).executor,
        )
        self._audit = LlmPromptAuditService(sessions)

    async def analyze(self, payload, *, rule_id, model_id, audit_context):
        if audit_context.rule_id != rule_id or audit_context.model_id != model_id:
            raise LlmAdapterError("Prompt audit context mismatch", code="CONFIG_ERROR")
        return await self._runtime.execute(
            "auto_tag.classify", input=payload, model_id=model_id,
            context=ExecutionContext(run_id=audit_context.run_id, attempt=audit_context.attempt),
            audit=self._audit, audit_context=audit_context,
        )
