"""One attempt with durable intent and auditable completion."""
from __future__ import annotations

import asyncio
import math
import hmac
from collections.abc import Mapping
from .contract import LlmError, LlmResponse


async def drain_on_cancel(task):
    """Keep ownership until real execution ends, even after repeated cancellation."""
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
        except Exception:
            if cancelled:
                raise asyncio.CancelledError() from None
            raise
    if cancelled:
        raise asyncio.CancelledError()
    return result


def field(value, name):
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def provider_error(error):
    status = getattr(error, "status_code", None)
    code = ("AUTH_FAILED" if status in {401, 403} else "RATE_LIMITED" if status == 429
            else "CONFIG_INVALID" if status in {400, 404, 422}
            else "TIMEOUT" if isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower()
            else "PROVIDER_FAILED")
    delay = None
    response = getattr(error, "response", None)
    headers = field(response, "headers") or getattr(error, "headers", None)
    if isinstance(headers, Mapping):
        try:
            value = float(headers.get("retry-after", ""))
            if math.isfinite(value) and 0 <= value <= 86400:
                delay = value
        except (ValueError, TypeError):
            pass
    return LlmError(code, retryable=code in {"RATE_LIMITED", "TIMEOUT", "PROVIDER_FAILED"}, retry_after_seconds=delay)


class LlmClient:
    def __init__(self, recorder, credentials):
        self.recorder, self.credentials = recorder, credentials
        self.providers = {}
        self._tasks = set()
        self._accepting = True

    def register(self, name, provider):
        if name in self.providers:
            raise ValueError("Provider already registered")
        self.providers[name] = provider

    async def generate(self, request, connection, context, *, admission):
        if not self._accepting:
            raise LlmError("CANCELLED")
        # Tasks and any async provider resources belong to this lifespan/loop.
        task = asyncio.create_task(self._generate(request, connection, context, admission))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return await drain_on_cancel(task)

    async def _generate(self, request, connection, context, admission):
        provider = self.providers.get(connection.provider_driver)
        if provider is None:
            raise LlmError("CONFIG_INVALID")
        try:
            credential = await asyncio.to_thread(self.credentials.resolve, connection.credential_ref)
        except Exception:
            raise LlmError("AUTH_FAILED") from None
        if not isinstance(credential, str) or not credential.strip():
            raise LlmError("AUTH_FAILED")
        try:
            call_id = await asyncio.to_thread(self.recorder.begin, request, connection, context)
        except LlmError:
            raise
        except Exception:
            raise LlmError("AUDIT_BEGIN_FAILED") from None
        state = "NOT_SENT"

        def before_dispatch():
            nonlocal state
            if state != "NOT_SENT":
                raise LlmError("CONFIG_INVALID")
            # Persist before checking dynamic authorization. Admission rejection
            # is positively NOT_SENT; a crash between intent and send is unknown.
            try:
                self.recorder.dispatch(call_id)
            except Exception:
                raise LlmError("AUDIT_BEGIN_FAILED") from None
            # Bind admission to the material actually injected into the SDK,
            # not only to the authorization reader's earlier snapshot (ABA).
            try:
                current_credential = self.credentials.resolve(connection.credential_ref)
                if not isinstance(current_credential, str) or not hmac.compare_digest(credential, current_credential):
                    raise LlmError("AUTH_FAILED")
            except Exception:
                raise LlmError("AUTH_FAILED") from None
            if admission.admit(connection, context) is not True:
                raise LlmError("CANCELLED")
            state = "MAY_HAVE_EXECUTED"

        response = None
        error = None
        try:
            raw = await provider.complete(request, connection, credential, before_dispatch=before_dispatch)
            state = "RESPONSE_RECEIVED"
            choices = field(raw, "choices")
            choice = choices[0] if isinstance(choices, (list, tuple)) and len(choices) == 1 else None
            content = field(field(choice, "message"), "content")
            if not isinstance(content, str) or not content.strip():
                raise LlmError("RESPONSE_INVALID")
            usage = field(raw, "usage")
            counts = tuple((name, value) for name in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens")
                           if type(value := field(usage, name)) is int and 0 <= value < 2**63)
            response = LlmResponse(call_id, content, str(field(choice, "finish_reason") or ""),
                                   str(field(raw, "model") or connection.model),
                                   field(raw, "id") if isinstance(field(raw, "id"), str) else None, counts)
        except LlmError as caught:
            error = caught
        except Exception as caught:
            error = provider_error(caught)
        if error:
            error.call_id, error.dispatch_state = call_id, state
        try:
            await asyncio.to_thread(self.recorder.finish, call_id, response=response, error=error, dispatch_state=state)
        except Exception:
            raise LlmError("AUDIT_FINISH_FAILED", call_id=call_id, dispatch_state=state) from None
        if error:
            raise error
        return response

    async def close(self):
        self._accepting = False
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        for provider in self.providers.values():
            await provider.close()
