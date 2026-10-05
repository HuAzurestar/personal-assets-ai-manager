"""Saved model conversion and dynamic authorization, independent of tag semantics."""
from __future__ import annotations

import hashlib
import hmac
import os
from threading import RLock
from functools import wraps

from backend.mapper.setting_mapper import SettingMapper
from backend.schema.setting import AutomationModelWrite
from middleware.llm.contract import ConnectionSnapshot, LlmError, LlmRequest, canonical

AUTHORIZATION_LOCK = RLock()


def synchronized_authorization(method):
    @wraps(method)
    def call(*args, **kwargs):
        with AUTHORIZATION_LOCK:
            return method(*args, **kwargs)
    return call


def read_model(sessions, model_id):
    with sessions() as db:
        setting = SettingMapper(db).get()
    try:
        models = (setting or {}).get("value", {}).get("automation", {}).get("models", [])
        profile = next((AutomationModelWrite.model_validate(m) for m in models if m.get("id") == model_id), None)
    except (ValueError, TypeError, AttributeError):
        raise LlmError("CONFIG_INVALID") from None
    if profile is None:
        raise LlmError("CONFIG_INVALID")
    if not profile.enabled:
        raise LlmError("MODEL_DISABLED")
    return profile


def connection_for(profile, *, timeout=None):
    params = profile.litellm_params.model_dump(exclude_none=True)
    proxy = params.pop("proxy_url", None) or os.getenv("PAAM_LLM_PROXY") or None
    params["proxy_url"] = proxy
    token = hashlib.sha256(canonical(params).encode()).hexdigest()
    return ConnectionSnapshot(profile.id, profile.id, token, profile.litellm_params.model,
                              profile.litellm_params.api_base, f"model:{profile.id}",
                              proxy_url=proxy, timeout=timeout or profile.litellm_params.timeout or 60.0,
                              allowed_target=profile.litellm_params.api_base)


def request_for(messages, profile, *, response_format=None):
    options = profile.litellm_params.model_dump(exclude_none=True)
    for name in ("model", "api_base", "timeout", "proxy_url", "allow_insecure_http"):
        options.pop(name, None)
    return LlmRequest.build(messages, response_format=response_format, generation_options=options)


class SavedModelAdmission:
    def __init__(self, sessions, reader, connection, *, run_control=None):
        self.sessions, self.reader, self.connection = sessions, reader, connection
        self.run_control = run_control
        # Never persisted/exported as configuration or prompt content.
        try:
            self._credential = reader.get_for_provider(connection.model_profile_id)
        except Exception:
            raise LlmError("CONFIG_INVALID") from None

    def admit(self, connection, operation):
        with AUTHORIZATION_LOCK:
            current = connection_for(read_model(self.sessions, connection.model_profile_id))
            if current.effective_token != self.connection.effective_token:
                raise LlmError("CONFIG_INVALID")
            secret = self.reader.get_for_provider(connection.model_profile_id)
            if not secret or not self._credential or not hmac.compare_digest(secret, self._credential):
                raise LlmError("AUTH_FAILED")
            if self.run_control is not None:
                self.run_control.admit_request()
        return True


LEGACY_CODES = {"CONFIG_INVALID": "CONFIG_ERROR", "AUTH_FAILED": "AUTH_ERROR",
                "TIMEOUT": "REQUEST_TIMEOUT", "RATE_LIMITED": "RATE_LIMIT",
                "PROVIDER_FAILED": "PROVIDER_UNAVAILABLE", "RESPONSE_INVALID": "OUTPUT_UNEXPECTED",
                "MODEL_DISABLED": "MODEL_DISABLED", "CANCELLED": "CANCELLED",
                "AUDIT_BEGIN_FAILED": "AUDIT_STORAGE_ERROR", "AUDIT_FINISH_FAILED": "AUDIT_FINISH_FAILED",
                "OPERATION_BLOCKED": "OPERATION_BLOCKED", "MODEL_REFUSED": "MODEL_REFUSED",
                "RESPONSE_TRUNCATED": "OUTPUT_TRUNCATED"}
