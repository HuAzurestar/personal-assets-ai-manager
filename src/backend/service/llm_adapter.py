"""Compatibility entry for auto-tag callers; execution lives in middleware."""
from __future__ import annotations

from collections.abc import Callable
from backend.error import LlmAdapterError
from backend.schema.llm_analysis import ProtectedLlmAnalysisInput, SyntheticLlmAnalysisInput
from backend.service.llm_privacy_service import require_protected_payload
from backend.service.auto_tag_task import (
    AUTO_TAG_TASK, ResponseMode, build_messages, parse_business_output, parse_provider_response,
)
from backend.middleware.prompt import bundled_prompt
from backend.middleware.provider import (
    _direct_litellm_completion, _field, _provider_exception, _retry_after_seconds,
)
from backend.middleware.runtime import TaskExecutor, build_request

Completion = Callable[..., object]


def build_litellm_request(payload, profile, *, response_mode="json_object"):
    return build_request(AUTO_TAG_TASK, payload, profile, bundled_prompt(AUTO_TAG_TASK.key), response_mode)


class LiteLlmAdapter:
    def __init__(self, completion: Completion | None = None):
        self._completion = completion

    @property
    def executor(self):
        return TaskExecutor(self._completion or _direct_litellm_completion)

    def analyze_synthetic(self, payload, profile, *, api_key, response_mode="json_object"):
        if not isinstance(payload, SyntheticLlmAnalysisInput):
            raise LlmAdapterError("Synthetic analysis requires a fixture DTO", code="CONFIG_ERROR")
        return self._analyze(payload, profile, api_key=api_key, response_mode=response_mode)

    def analyze_protected(self, payload, profile, *, api_key, response_mode="json_object",
                          audit=None, audit_context=None):
        try:
            require_protected_payload(payload)
        except ValueError:
            raise LlmAdapterError("The protected input boundary rejected this request", code="CONFIG_ERROR") from None
        return self._analyze(payload, profile, api_key=api_key, response_mode=response_mode,
                             audit=audit, audit_context=audit_context)

    def _analyze(self, payload, profile, *, api_key, response_mode, audit=None, audit_context=None):
        return self.executor.execute(
            AUTO_TAG_TASK, payload, profile, bundled_prompt(AUTO_TAG_TASK.key),
            api_key=api_key, response_mode=response_mode, audit=audit, audit_context=audit_context,
        )
