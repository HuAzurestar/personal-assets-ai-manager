"""Task execution owns provider requests, validation and per-attempt recording."""
from __future__ import annotations

import asyncio
import re
from time import monotonic

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.error import LlmAdapterError
from backend.middleware.provider import (
    _direct_litellm_completion, _provider_content, _provider_exception, response_content,
)
from backend.middleware.service.invocation_service import InvocationService
from backend.middleware.service.model_service import ModelService
from backend.middleware.service.prompt_service import PromptService
from backend.middleware.usage import Usage, extract_usage
from backend.schema.setting import AutomationModelWrite


class ExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: str = Field(default="", pattern=r"^[a-zA-Z0-9:_-]{0,100}$")
    attempt: int = Field(default=1, ge=1, le=100)


def build_request(task, payload, profile, prompt, response_mode="json_object"):
    if response_mode not in ("json_object", "json_schema"):
        raise LlmAdapterError("The configured response mode is unsupported", code="CONFIG_ERROR")
    try:
        profile = AutomationModelWrite.model_validate(profile.model_dump())
    except (ValidationError, ValueError, TypeError):
        raise LlmAdapterError("The provider parameters failed the safety boundary", code="CONFIG_ERROR") from None
    prepared = task.prepare(payload, prompt, response_mode)
    return {
        **profile.litellm_params.model_dump(exclude_none=True),
        "stream": False, "num_retries": 0, "timeout": profile.litellm_params.timeout or 60.0,
        "no-log": True, "messages": prepared.messages, "response_format": prepared.response_format,
    }


class TaskExecutor:
    def __init__(self, completion=None):
        self.completion = completion

    def execute(self, task, payload, profile, prompt, *, api_key,
                response_mode="json_object", invocations=None, context=ExecutionContext(),
                audit=None, audit_context=None):
        if not profile.enabled or not isinstance(api_key, str) or not api_key.strip():
            raise LlmAdapterError("The configured model or credential is unavailable", code="CONFIG_ERROR")
        if (audit is None) != (audit_context is None):
            raise LlmAdapterError("The prompt audit context is incomplete", code="CONFIG_ERROR")
        request = build_request(task, payload, profile, prompt, response_mode)
        invocation_id = None
        audit_id = None
        started = monotonic()
        response_text = ""
        usage = Usage()

        def finish(status, *, error_code="", result_code=""):
            # Both observers finish before the result can reach a business commit.
            if invocations is not None and invocation_id is not None:
                invocations.finish(
                    invocation_id, status=status, error_code=error_code, result_code=result_code,
                    response_text=response_text, usage=usage,
                    latency_ms=max(0, int((monotonic() - started) * 1000)),
                )
            if audit is not None and audit_id is not None:
                audit.finish(audit_id, status=result_code if status == "SUCCEEDED" else status,
                             error_code=error_code, response_text=response_text)

        try:
            if invocations is not None:
                invocation_id = invocations.start(task=task, prompt=prompt, profile=profile,
                                                  context=context, request=request)
            if audit is not None:
                audit_id = audit.start(audit_context, model_name=profile.litellm_params.model, request=request)
            response = (self.completion or _direct_litellm_completion)(**request, api_key=api_key)
        except LlmAdapterError as error:
            finish("ERROR", error_code=error.code)
            raise
        except Exception as error:
            safe_error = _provider_exception(error)
            finish("ERROR", error_code=safe_error.code)
            raise safe_error from None
        response_text = _provider_content(response)
        usage = extract_usage(response)
        try:
            result = task.parse(response_content(response), payload)
            if not isinstance(result, task.output_type):
                raise LlmAdapterError("The AI output type is invalid", code="OUTPUT_SCHEMA_INVALID")
            result_code = task.result_status(result)
            if not isinstance(result_code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", result_code):
                raise LlmAdapterError("The AI result code is invalid", code="OUTPUT_SCHEMA_INVALID")
        except LlmAdapterError as error:
            finish("REJECTED", error_code=error.code)
            raise
        except Exception:
            finish("REJECTED", error_code="OUTPUT_SCHEMA_INVALID")
            raise LlmAdapterError("The AI output failed validation", code="OUTPUT_SCHEMA_INVALID") from None
        finish("SUCCEEDED", result_code=result_code)
        return result


class AiRuntime:
    def __init__(self, sessions, secret_reader, registry, executor=None):
        self.sessions = sessions
        self.registry = registry
        self.models = ModelService(sessions, secret_reader)
        self.invocations = InvocationService(sessions)
        self.executor = executor or TaskExecutor()

    async def execute(self, task, *, input, model_id, context=ExecutionContext(),
                      response_mode="json_object", audit=None, audit_context=None):
        definition = self.registry.resolve(task)
        if not isinstance(input, definition.input_type):
            raise LlmAdapterError("The AI input type is invalid", code="CONFIG_ERROR")
        # Resolve snapshots and credentials in worker-owned sessions; no SQL
        # transaction stays open while the provider is running.
        return await asyncio.to_thread(
            self._execute, definition, input, model_id, context, response_mode, audit, audit_context,
        )

    def _execute(self, definition, payload, model_id, context, response_mode, audit, audit_context):
        profile = self.models.resolve(model_id)
        with self.sessions() as db:
            prompt = PromptService(db, self.registry).resolve(definition.key)
        secret = self.models.credential(model_id)
        return self.executor.execute(
            definition, payload, profile, prompt, api_key=secret, context=context,
            response_mode=response_mode, invocations=self.invocations,
            audit=audit, audit_context=audit_context,
        )
