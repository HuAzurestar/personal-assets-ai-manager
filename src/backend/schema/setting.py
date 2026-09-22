from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from backend.schema.response import SuccessResponse


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
    "tools",
}


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
            ):
                raise ValueError(
                    f"provider parameter {'.'.join((*path, raw_key))} is forbidden"
                )
            _validate_provider_value(child, path=(*path, raw_key))
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_provider_value(child, path=(*path, str(index)))
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("provider parameters must contain finite numbers")
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    raise ValueError("provider parameters must contain JSON-compatible values")


class LiteLLMParams(BaseModel):
    """Validated LiteLLM configuration while retaining provider extensions."""

    model_config = ConfigDict(extra="allow")

    model: str = Field(min_length=1, max_length=512)
    api_base: str = Field(min_length=1, max_length=2048)
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
        if value.strip() != value or not value:
            raise ValueError("model must be non-empty and cannot have outer whitespace")
        return value

    @field_validator("api_base")
    @classmethod
    def validate_api_base(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("api_base must be an HTTPS URL without credentials or fragment")
        return value


class AutomationModelWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(strict=True, ge=1)
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


class AutomationSettingUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_updated_time: datetime | None
    models: list[AutomationModelWrite]

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
        model_ids = [model.id for model in self.models]
        if len(set(model_ids)) != len(model_ids):
            raise ValueError("model ids must be unique")
        return self


class AutomationSettingRead(BaseModel):
    models: list[AutomationModelRead]
    disclosure: dict[str, Any]
    updated_time: datetime | None


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


class ModelConnectionTestRead(BaseModel):
    model_id: int
    connected: Literal[False]
    mode: Literal["SIMULATED"]
    key_configured: bool
    message: str


class ModelConnectionTestResponse(SuccessResponse[ModelConnectionTestRead]):
    body: ModelConnectionTestRead
