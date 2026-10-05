"""Resolve one configured model and invoke the protected LiteLLM boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol

from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.core import protected_secret_store
from backend.core.feature_observability import observed
from backend.error import LlmAdapterError, ProtectedSecretStoreError
from backend.mapper.setting_mapper import SettingMapper
from backend.schema.llm_analysis import (
    LlmAnalysisInput,
    LlmAnalysisResult,
    ProtectedLlmAnalysisInput,
)
from backend.schema.setting import AutomationModelWrite
from backend.service.llm_adapter import LiteLlmAdapter
from backend.service.llm_prompt_audit_service import LlmPromptAuditService, PromptAuditContext


class ProviderSecretReader(Protocol):
    def get_for_provider(self, model_id: int) -> str | None: ...


provider_secret_reader = protected_secret_store


class ConfiguredLlmAnalyzer:
    def __init__(
        self,
        sessions: Callable[[], Session],
        secret_store: ProviderSecretReader,
        adapter: LiteLlmAdapter | None = None,
    ):
        self._sessions = sessions
        self._secret_store = secret_store
        self._adapter = adapter or LiteLlmAdapter()
        self._audit = LlmPromptAuditService(sessions)

    @observed("LLM_ADAPTER", "adapter_duration")
    async def analyze(
        self,
        payload: LlmAnalysisInput,
        *,
        rule_id: int,
        model_id: int,
        audit_context: PromptAuditContext,
    ) -> LlmAnalysisResult:
        if audit_context.rule_id != rule_id or audit_context.model_id != model_id:
            raise LlmAdapterError("Prompt audit context mismatch", code="CONFIG_ERROR")
        if not isinstance(payload, ProtectedLlmAnalysisInput):
            raise LlmAdapterError(
                "Scheduled analysis requires a protected Ledger payload",
                code="CONFIG_ERROR",
            )
        profile = await asyncio.to_thread(self._profile, model_id)
        try:
            secret = await asyncio.to_thread(self._secret_store.get_for_provider, model_id)
        except ProtectedSecretStoreError:
            raise LlmAdapterError(
                "The configured credential store is unavailable", code="CONFIG_ERROR",
            ) from None
        if not secret:
            raise LlmAdapterError(
                "The configured model credential is missing",
                code="CONFIG_ERROR",
            )
        return await asyncio.to_thread(
            self._adapter.analyze_protected,
            payload,
            profile,
            api_key=secret,
            audit=self._audit,
            audit_context=audit_context,
        )

    def _profile(self, model_id: int) -> AutomationModelWrite:
        with self._sessions() as db:
            setting = SettingMapper(db).get()
        try:
            value = setting["value"] if setting is not None else {}
            automation = value.get("automation", {})
            raw_models = automation.get("models", [])
            models = [AutomationModelWrite.model_validate(item) for item in raw_models]
        except (AttributeError, TypeError, ValueError, ValidationError):
            raise LlmAdapterError(
                "The stored model configuration is invalid",
                code="CONFIG_ERROR",
            ) from None
        profile = next((item for item in models if item.id == model_id), None)
        if profile is None or not profile.enabled:
            raise LlmAdapterError(
                "The configured model is unavailable",
                code="CONFIG_ERROR",
            )
        return profile
