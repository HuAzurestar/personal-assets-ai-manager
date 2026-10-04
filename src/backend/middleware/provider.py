"""Provider transport and response envelopes; no business task imports."""
from __future__ import annotations

import atexit
import logging
import math
import os
from collections.abc import Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import RLock

import httpx
from backend.error import LlmAdapterError
from backend.schema.setting import LiteLLMParams

_LITELLM_LOCK = RLock()
_LITELLM_HTTP_CLIENT: httpx.Client | None = None
_PROXY_HTTP_CLIENTS: dict[str, httpx.Client] = {}

def _provider_content(response: object) -> str:
    """Capture message content only, never SDK headers or exception objects."""
    choices = _field(response, "choices")
    if not isinstance(choices, (list, tuple)) or not choices:
        return ""
    content = _field(_field(choices[0], "message"), "content")
    return content if isinstance(content, str) else ""


def response_content(response: object) -> str:
    status_code = _field(response, "status_code")
    if isinstance(status_code, int) and status_code != 200:
        raise _http_error(status_code, _field(response, "headers"))

    choices = _field(response, "choices")
    if not isinstance(choices, (list, tuple)) or len(choices) != 1:
        raise _error(
            "OUTPUT_UNEXPECTED", "Provider returned an unexpected choice count"
        )
    choice = choices[0]
    index = _field(choice, "index")
    if index not in (None, 0):
        raise _error(
            "OUTPUT_UNEXPECTED", "Provider returned an unexpected choice index"
        )
    message = _field(choice, "message")
    if message is None:
        raise _error("OUTPUT_UNEXPECTED", "Provider response is missing a message")
    if _field(message, "role") not in (None, "assistant"):
        raise _error(
            "OUTPUT_UNEXPECTED", "Provider returned an unexpected message role"
        )
    if _field(message, "refusal"):
        raise _error("MODEL_REFUSED", "The model refused this request")
    finish_reason = _field(choice, "finish_reason")
    if finish_reason == "length":
        raise _error("OUTPUT_TRUNCATED", "The model output was truncated")
    if finish_reason != "stop" or _field(message, "tool_calls"):
        raise _error("OUTPUT_UNEXPECTED", "Provider returned an unsupported response")
    content = _field(message, "content")
    if not isinstance(content, str) or not content.strip():
        raise _error("OUTPUT_EMPTY", "The model output is empty")
    return content


def _direct_litellm_completion(*, _lock_timeout=-1, **request):
    """Keep cached provider clients' transport alive for the process lifetime."""

    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")
    os.environ["LITELLM_LOG"] = "ERROR"
    import litellm

    global _LITELLM_HTTP_CLIENT
    proxy_url = request.pop("proxy_url", None) or os.getenv("PAAM_LLM_PROXY") or None
    if proxy_url is not None:
        try:
            proxy_url = LiteLLMParams.validate_proxy_url(proxy_url)
        except (TypeError, ValueError):
            raise _error("CONFIG_ERROR", "The configured proxy URL is invalid") from None
    if not _LITELLM_LOCK.acquire(timeout=_lock_timeout):
        raise TimeoutError("Provider transport is busy")
    try:
        _disable_provider_logging(litellm)
        if proxy_url:
            if proxy_url not in _PROXY_HTTP_CLIENTS:
                _PROXY_HTTP_CLIENTS[proxy_url] = httpx.Client(proxy=proxy_url, trust_env=False)
            client = _PROXY_HTTP_CLIENTS[proxy_url]
        else:
            if _LITELLM_HTTP_CLIENT is None:
                _LITELLM_HTTP_CLIENT = httpx.Client(trust_env=False)
            client = _LITELLM_HTTP_CLIENT
        previous = litellm.client_session
        litellm.client_session = client
        try:
            return litellm.completion(**{
                **request, "no-log": True, "num_retries": 0, "max_retries": 0,
            })
        finally:
            litellm.client_session = previous
    finally:
        _LITELLM_LOCK.release()


def _disable_provider_logging(litellm) -> None:
    """PAAM owns the SDK; never inherit environment/callback tracing of bills."""
    for name, value in {
        "set_verbose": False, "suppress_debug_info": True, "telemetry": False,
        "turn_off_message_logging": True, "log_raw_request_response": False,
        "callbacks": [], "input_callback": [], "success_callback": [],
        "failure_callback": [], "service_callback": [], "audit_log_callbacks": [],
        "_async_input_callback": [], "_async_success_callback": [], "_async_failure_callback": [],
    }.items():
        setattr(litellm, name, value)
    for name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy", "httpx", "httpcore", "openai"):
        provider_logger = logging.getLogger(name)
        provider_logger.handlers = [logging.NullHandler()]
        provider_logger.setLevel(logging.CRITICAL + 1)
        provider_logger.propagate = False
        provider_logger.disabled = True


def _close_litellm_http_client() -> None:
    # LiteLLM caches SDK clients that retain this transport. Do not close it
    # after a request or an ASGI lifespan restart in the same process.
    with _LITELLM_LOCK:
        if _LITELLM_HTTP_CLIENT is not None:
            _LITELLM_HTTP_CLIENT.close()
        for client in _PROXY_HTTP_CLIENTS.values():
            client.close()


atexit.register(_close_litellm_http_client)


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _provider_exception(error: Exception) -> LlmAdapterError:
    status_code = getattr(error, "status_code", None)
    headers = getattr(error, "headers", None)
    if headers is None:
        headers = _field(getattr(error, "response", None), "headers")
    retry_after = _retry_after_seconds(headers)
    details = {"retry_after_seconds": retry_after} if retry_after is not None else None
    if status_code in (401, 403):
        return _error("AUTH_ERROR", "The model provider rejected the credential")
    if status_code == 429:
        return _error(
            "RATE_LIMIT", "The model provider rate limit was reached", retryable=True,
            details=details,
        )
    if isinstance(status_code, int) and status_code >= 500:
        return _error(
            "PROVIDER_UNAVAILABLE", "The model provider is unavailable", retryable=True,
            details=details,
        )
    if isinstance(status_code, int) and status_code in (400, 404, 422):
        return _error("CONFIG_ERROR", "The model provider rejected the configuration")
    if isinstance(error, TimeoutError) or "timeout" in type(error).__name__.casefold():
        return _error(
            "REQUEST_TIMEOUT", "The model provider request timed out", retryable=True
        )
    return _error(
        "PROVIDER_UNAVAILABLE", "The model provider request failed", retryable=True
    )


def _http_error(status_code: int, headers: object = None) -> LlmAdapterError:
    class ProviderStatusError(Exception):
        pass

    error = ProviderStatusError()
    error.status_code = status_code  # type: ignore[attr-defined]
    error.headers = headers  # type: ignore[attr-defined]
    return _provider_exception(error)


def _retry_after_seconds(headers: object) -> float | None:
    if not isinstance(headers, Mapping):
        return None
    raw = headers.get("retry-after", headers.get("Retry-After"))
    if not isinstance(raw, str) or len(raw) > 128:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            deadline = parsedate_to_datetime(raw)
            if deadline.tzinfo is None:
                return None
            seconds = (deadline - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    if not math.isfinite(seconds):
        return None
    # Only this numeric delay crosses the boundary, never headers/provider text.
    return min(86400.0, max(0.0, seconds))


def _error(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, object] | None = None,
) -> LlmAdapterError:
    return LlmAdapterError(
        message,
        code=code,
        retryable=retryable,
        details=details,
    )
