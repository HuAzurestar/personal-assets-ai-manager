"""Immutable public call contracts. No ORM, business DTO or SDK imports."""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


OPTIONS = frozenset({"temperature", "top_p", "max_tokens", "max_completion_tokens", "seed", "extra_body"})
EXTENSIONS = frozenset({"enable_thinking"})
ERROR_CODES = frozenset({"CONFIG_INVALID", "AUTH_FAILED", "MODEL_DISABLED", "CANCELLED",
                         "TIMEOUT", "RATE_LIMITED", "PROVIDER_FAILED", "RESPONSE_INVALID",
                         "AUDIT_BEGIN_FAILED", "AUDIT_FINISH_FAILED", "OPERATION_BLOCKED", "UNKNOWN_ERROR"})


@dataclass(frozen=True)
class LlmRequest:
    # Canonical JSON is a deep immutable snapshot, unlike a frozen mutable dict.
    json: str = field(repr=False)

    @classmethod
    def build(cls, messages, *, response_format=None, generation_options=None):
        return cls(canonical({"messages": messages, "response_format": response_format,
                              "generation_options": generation_options or {}}))

    def __post_init__(self):
        try:
            value = json.loads(self.json)
            if set(value) != {"messages", "response_format", "generation_options"}:
                raise ValueError()
            messages, options = value["messages"], value["generation_options"]
            if not isinstance(messages, list) or not 1 <= len(messages) <= 64:
                raise ValueError()
            for message in messages:
                if set(message) != {"role", "content"} or message["role"] not in {"system", "user", "assistant"}:
                    raise ValueError()
                if not isinstance(message["content"], str) or len(message["content"].encode("utf-8")) > 256 * 1024:
                    raise ValueError()
            if not isinstance(options, dict) or set(options) - OPTIONS:
                raise ValueError()
            for name, item in options.items():
                if name == "extra_body":
                    if not isinstance(item, dict) or set(item) - EXTENSIONS or any(type(v) is not bool for v in item.values()):
                        raise ValueError()
                elif type(item) not in (int, float) or not math.isfinite(item):
                    raise ValueError()
                elif name in {"max_tokens", "max_completion_tokens"} and (type(item) is not int or not 1 <= item <= 65536):
                    raise ValueError()
                elif name == "temperature" and not 0 <= item <= 2:
                    raise ValueError()
                elif name == "top_p" and not 0 < item <= 1:
                    raise ValueError()
                elif name == "seed" and (type(item) is not int or not -2**31 <= item < 2**31):
                    raise ValueError()
            fmt = value["response_format"]
            if fmt is not None and (not isinstance(fmt, dict) or fmt.get("type") not in {"text", "json_object", "json_schema"}):
                raise ValueError()
            if len(self.json.encode("utf-8")) > 512 * 1024:
                raise ValueError()
            object.__setattr__(self, "json", canonical(value))
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            raise LlmError("CONFIG_INVALID") from None

    def unpack(self):
        return json.loads(self.json)


@dataclass(frozen=True)
class ConnectionSnapshot:
    connection_id: int
    model_profile_id: int
    effective_token: str
    model: str
    api_base: str
    credential_ref: str
    proxy_url: str | None = None
    timeout: float = 60.0
    provider_driver: str = "litellm"
    # Exact saved endpoint is the authorized target; private/local destinations
    # are supported when explicitly saved rather than inferred from input.
    allowed_target: str = ""

    def __post_init__(self):
        parsed = urlsplit(self.api_base)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or re.search(r"[\s%]", self.api_base)
                or self.allowed_target != self.api_base
                or type(self.timeout) not in (int, float) or not math.isfinite(self.timeout)
                or not 0 < self.timeout <= 120):
            raise LlmError("CONFIG_INVALID")
        if self.proxy_url:
            proxy = urlsplit(self.proxy_url)
            if (proxy.scheme not in {"http", "https"} or not proxy.hostname
                    or proxy.username is not None or proxy.password is not None
                    or proxy.path not in {"", "/"} or proxy.query or proxy.fragment):
                raise LlmError("CONFIG_INVALID")


@dataclass(frozen=True)
class CallContext:
    source: str
    operation_id: str | None = None
    job_run_id: str | None = None
    associations: tuple[tuple[str, int], ...] = ()
    prompt_id: str | None = None
    prompt_fingerprint: str | None = None

    def __post_init__(self):
        if not re.fullmatch(r"[a-z][a-z0-9_.:-]{0,63}", self.source):
            raise LlmError("CONFIG_INVALID")
        for value in (self.operation_id, self.job_run_id, self.prompt_id, self.prompt_fingerprint):
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,160}", value)):
                raise LlmError("CONFIG_INVALID")
        if (type(self.associations) is not tuple or len(self.associations) > 4
                or any(k not in {"rule_id", "rule_revision", "ledger_id", "model_id"}
                       or type(v) is not int or not 1 <= v < 2**63 for k, v in self.associations)
                or len(dict(self.associations)) != len(self.associations)):
            raise LlmError("CONFIG_INVALID")


@dataclass(frozen=True)
class LlmResponse:
    call_id: int
    content: str = field(repr=False)
    finish_reason: str
    model: str
    provider_request_id: str | None = None
    usage: tuple[tuple[str, int], ...] = ()


class LlmError(Exception):
    def __init__(self, code, *, call_id=None, dispatch_state="NOT_SENT", retryable=False, retry_after_seconds=None):
        # Only fixed safe facts cross the error boundary.
        code = code if isinstance(code, str) and code in ERROR_CODES else "UNKNOWN_ERROR"
        if dispatch_state not in {"NOT_SENT", "MAY_HAVE_EXECUTED", "RESPONSE_RECEIVED"}:
            dispatch_state = "MAY_HAVE_EXECUTED"
        if call_id is not None and (type(call_id) is not int or not 0 < call_id < 2**63):
            call_id = None
        super().__init__(code)
        self.code, self.call_id, self.dispatch_state = code, call_id, dispatch_state
        self.retryable, self.retry_after_seconds = retryable, retry_after_seconds


class CredentialPort(Protocol):
    def resolve(self, credential_ref: str) -> str: ...


class RequestAdmission(Protocol):
    def admit(self, connection: ConnectionSnapshot, operation: CallContext) -> bool: ...


class CallRecorderPort(Protocol):
    def begin(self, request: LlmRequest, connection: ConnectionSnapshot, context: CallContext) -> int: ...
    def dispatch(self, call_id: int) -> None: ...
    def finish(self, call_id: int, *, response: LlmResponse | None, error: LlmError | None, dispatch_state: str) -> None: ...


class LlmProvider(Protocol):
    async def complete(self, request, connection, credential, *, before_dispatch) -> object: ...
    async def close(self) -> None: ...
