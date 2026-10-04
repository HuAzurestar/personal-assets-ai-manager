from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from backend.core.money import MAX_ABS_AMOUNT, normalize_currency_code
from backend.schema.response import SuccessResponse
from backend.schema.runtime_config import RuntimeConfigRead


_FORBIDDEN_PARAMETER_KEYS = {
    "api_key",
    "callback",
    "callbacks",
    "callable",
    "class",
    "class_name",
    "class_path",
    "messages",
    "module",
    "fallbacks",
    "context_window_fallbacks",
    "content_policy_fallbacks",
    "response_format",
    "stream",
    "tools",
    "prompt", "system", "metadata", "user", "headers", "extra_headers",
    "client", "client_session", "logger_fn", "logging_obj", "mock_response",
    "num_retries", "max_retries", "cache", "caching", "base_url",
    "set_verbose", "suppress_debug_info", "log_raw_request_response", "drop_params",
}

_TEXT_PARAMETER_ENUMS = {"reasoning_effort": {"none", "minimal", "low", "medium", "high", "xhigh"}}


def _validate_provider_value(value: Any, *, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            if not isinstance(raw_key, str):
                raise ValueError("provider parameter keys must be strings")
            key = raw_key.casefold()
            if (
                key in _FORBIDDEN_PARAMETER_KEYS
                or key.endswith("_api_key")
                or "password" in key
                or "secret" in key
                or key.startswith("__")
                or any(part in key for part in ("callback", "logging", "telemetry", "trace"))
                or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key)
                or re.search(r"\d{7,}", key)
                or (path and key in {"model", "api_base", "stream", "response_format"})
            ):
                raise ValueError("provider parameter is forbidden by the safety boundary")
            _validate_provider_value(child, path=(*path, raw_key))
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_provider_value(child, path=(*path, str(index)))
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("provider parameters must contain finite numbers")
    if isinstance(value, str) and path not in {("model",), ("api_base",), ("proxy_url",)}:
        if not path or value not in _TEXT_PARAMETER_ENUMS.get(path[-1], set()):
            raise ValueError("unrecognized provider text parameter cannot be sent safely")
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    raise ValueError("provider parameters must contain JSON-compatible values")


class LiteLLMParams(BaseModel):
    """Validated LiteLLM configuration while retaining provider extensions."""

    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1, max_length=512)
    api_base: str = Field(min_length=1, max_length=2048)
    proxy_url: str | None = Field(default=None, max_length=2048)
    allow_insecure_http: bool = Field(default=False, strict=True, exclude_if=lambda v: not v)
    temperature: float | None = Field(default=None, strict=True)
    max_tokens: int | None = Field(default=None, strict=True, ge=1)
    timeout: float | None = Field(default=None, strict=True, gt=0)
    extra_body: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_unsafe_parameters(cls, value: Any) -> Any:
        _validate_provider_value(value)
        return value

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]{0,511}", value):
            raise ValueError("model must be non-empty and cannot have outer whitespace")
        return value

    @field_validator("api_base")
    @classmethod
    def validate_api_base(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.query
            or re.search(r"[\s%]", value)
        ):
            raise ValueError("api_base must be an HTTP(S) URL without credentials or fragment")
        return value

    @model_validator(mode="after")
    def require_http_authorization(self):
        if urlsplit(self.api_base).scheme == "http" and not self.allow_insecure_http:
            raise ValueError("HTTP requires explicit authorization for this connection")
        return self

    @field_validator("proxy_url")
    @classmethod
    def validate_proxy_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment
            or re.search(r"[\s%]", value)
        ):
            raise ValueError("proxy_url must be an HTTP(S) proxy URL without credentials")
        return value.rstrip("/")


class AutomationModelWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(strict=True, ge=1)
    provider: Literal["custom", "siliconflow", "deepseek", "opencode_console"] = "custom"
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = Field(strict=True)
    litellm_params: LiteLLMParams

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if value.strip() != value:
            raise ValueError("name cannot have outer whitespace")
        return value


class AutomationModelRead(AutomationModelWrite):
    key_configured: bool


class AmountBandSetting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    boundaries: list[Annotated[int, Field(strict=True, ge=0, le=MAX_ABS_AMOUNT)]] = Field(
        min_length=1, max_length=64,
    )

    @field_validator("boundaries")
    @classmethod
    def validate_boundaries(cls, value: list[int]) -> list[int]:
        if value[0] != 0 or any(left >= right for left, right in zip(value, value[1:])):
            raise ValueError("amount boundaries must start at zero and strictly increase")
        return value


class AutomationDisclosure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_granularity: Literal["DAY", "MONTH", "NONE"] = "DAY"
    amount_bands: dict[str, AmountBandSetting] = Field(default_factory=dict, max_length=64)

    @field_validator("amount_bands")
    @classmethod
    def validate_currencies(
        cls, value: dict[str, AmountBandSetting],
    ) -> dict[str, AmountBandSetting]:
        for code in value:
            if normalize_currency_code(code) != code:
                raise ValueError("amount band currency codes must use canonical currency units")
        return value


class AutomationSettingUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_updated_time: datetime | None
    models: list[AutomationModelWrite] | None = None
    disclosure: AutomationDisclosure | None = None
    scan_enabled: bool | None = Field(default=None, strict=True)

    @field_validator("expected_updated_time")
    @classmethod
    def validate_expected_updated_time(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("expected_updated_time requires a timezone")
        return value

    @model_validator(mode="after")
    def reject_duplicate_model_ids(self) -> "AutomationSettingUpdateRequest":
        if self.models is None and self.disclosure is None and self.scan_enabled is None:
            raise ValueError("provide models, disclosure, or scan_enabled to update")
        sections = self.model_fields_set - {"expected_updated_time"}
        if any(getattr(self, field) is None for field in sections):
            raise ValueError("omit unchanged sections instead of sending null")
        model_ids = [model.id for model in self.models or []]
        if len(set(model_ids)) != len(model_ids):
            raise ValueError("model ids must be unique")
        return self


class AutomationSettingRead(BaseModel):
    models: list[AutomationModelRead]
    disclosure: AutomationDisclosure
    scan_enabled: bool
    scan_available: bool
    updated_time: datetime | None
    config_state: RuntimeConfigRead | None = Field(default=None, exclude_if=lambda value: value is None)


class AutomationSettingResponse(SuccessResponse[AutomationSettingRead]):
    body: AutomationSettingRead


class ModelSecretUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    secret: str = Field(min_length=1, max_length=8192)

    @field_validator("secret")
    @classmethod
    def validate_secret(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("secret cannot be blank")
        return value


class ModelSecretStateRead(BaseModel):
    model_id: int
    key_configured: bool


class ModelSecretStateResponse(SuccessResponse[ModelSecretStateRead]):
    body: ModelSecretStateRead


class ModelCatalogRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["custom", "siliconflow", "deepseek", "opencode_console"]
    api_base: str = Field(min_length=1, max_length=2048)
    proxy_url: str | None = Field(default=None, max_length=2048)
    allow_insecure_http: bool = Field(default=False, strict=True)
    model_id: int | None = Field(default=None, strict=True, ge=1)
    secret: str | None = Field(default=None, max_length=8192)

    @field_validator("api_base")
    @classmethod
    def validate_api_base(cls, value: str) -> str:
        return LiteLLMParams.validate_api_base(value)

    @model_validator(mode="after")
    def require_http_authorization(self):
        if urlsplit(self.api_base).scheme == "http" and not self.allow_insecure_http:
            raise ValueError("HTTP requires explicit authorization for this connection")
        return self

    @field_validator("proxy_url")
    @classmethod
    def validate_proxy_url(cls, value: str | None) -> str | None:
        return LiteLLMParams.validate_proxy_url(value)


class ModelCatalogItem(BaseModel):
    id: str
    name: str


class ModelCatalogRead(BaseModel):
    items: list[ModelCatalogItem]
    total: int
    chat_only: bool


class ModelCatalogResponse(SuccessResponse[ModelCatalogRead]):
    body: ModelCatalogRead


class ModelConnectionTestRead(BaseModel):
    model_id: int
    connected: Literal[False]
    mode: Literal["SIMULATED"]
    key_configured: bool
    message: str


class ModelConnectionTestResponse(SuccessResponse[ModelConnectionTestRead]):
    body: ModelConnectionTestRead


class ModelConnectionCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]
    expected_updated_time: datetime

    @field_validator("expected_updated_time")
    @classmethod
    def validate_expected_updated_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expected_updated_time requires a timezone")
        return value


class ModelConnectionCheckRead(BaseModel):
    model_id: int
    connected: bool
    code: str
    message: str
    checked_at: datetime
    configuration_updated_time: datetime
    mode: Literal["LIVE"] = "LIVE"


class ModelConnectionCheckResponse(SuccessResponse[ModelConnectionCheckRead]):
    body: ModelConnectionCheckRead
