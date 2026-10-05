"""Bounded, read-only model discovery for compatible chat providers."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable

import httpx
from sqlalchemy.orm import Session

from backend.error import ProtectedSecretStoreError, SettingError
from backend.mapper.setting_mapper import SettingMapper
from backend.schema.setting import (
    LiteLLMParams,
    ModelCatalogItem,
    ModelCatalogRead,
    ModelCatalogRequest,
)
from backend.service.configured_llm_analyzer import ProviderSecretReader


PRESET_BASES = {
    "siliconflow": "https://api.siliconflow.cn/v1",
    "deepseek": "https://api.deepseek.com",
    "opencode_console": "https://opencode.ai/inference/openai/v1",
}
_OPENCODE_CHAT_PREFIXES = (
    "deepseek-", "glm-", "kimi-", "minimax-", "mimo-", "longcat-",
    "ling-", "nemotron-", "space-bunny", "hy3", "hy4",
)
_OPENCODE_CHAT_EXACT = {"big-pickle", "qwen3.8-max"}
_MAX_CATALOG_BYTES = 1024 * 1024
_MAX_CATALOG_ITEMS = 1000
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:@-]{0,511}\Z")


class ModelCatalogService:
    def __init__(
        self,
        db: Session,
        secret_reader: ProviderSecretReader,
        client_factory: Callable[..., httpx.Client] = httpx.Client,
    ):
        self._db = db
        self._secret_reader = secret_reader
        self._client_factory = client_factory

    def list(self, request: ModelCatalogRequest) -> ModelCatalogRead:
        base = request.api_base.rstrip("/")
        if request.provider != "custom" and base != PRESET_BASES[request.provider]:
            raise SettingError(
                422, "供应商预设地址不匹配；自定义地址请选择自定义供应商",
                code="MODEL_CATALOG_BASE_MISMATCH",
            )
        secret = self._secret(request, base)
        # Do not hold a SQLite read transaction while waiting for a provider.
        self._db.rollback()
        raw_proxy = request.proxy_url or os.getenv("PAAM_LLM_PROXY") or None
        try:
            proxy = LiteLLMParams.validate_proxy_url(raw_proxy)
        except (TypeError, ValueError):
            raise SettingError(422, "代理地址无效", code="MODEL_PROXY_INVALID") from None
        url = (
            "https://opencode.ai/inference/v1/models"
            if request.provider == "opencode_console" else f"{base}/models"
        )
        params = {"sub_type": "chat"} if request.provider == "siliconflow" else None
        headers = {"Authorization": f"Bearer {secret}"} if secret else {}
        try:
            with self._client_factory(proxy=proxy, trust_env=False, timeout=12.0,
                                      follow_redirects=False) as client:
                with client.stream("GET", url, params=params, headers=headers) as response:
                    status = response.status_code
                    if status in {401, 403}:
                        raise SettingError(401, "模型列表鉴权失败，请核对 API Key", code="MODEL_CATALOG_AUTH_ERROR")
                    if status == 429:
                        raise SettingError(429, "供应商限制了模型列表请求", code="MODEL_CATALOG_RATE_LIMIT")
                    if status != 200:
                        raise SettingError(502, "供应商未返回模型列表", code="MODEL_CATALOG_UNAVAILABLE")
                    chunks = bytearray()
                    for chunk in response.iter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > _MAX_CATALOG_BYTES:
                            raise SettingError(502, "模型列表过大", code="MODEL_CATALOG_TOO_LARGE")
        except httpx.TimeoutException:
            raise SettingError(504, "获取模型列表超时，请检查网络或代理", code="MODEL_CATALOG_TIMEOUT") from None
        except httpx.HTTPError:
            raise SettingError(502, "无法连接模型列表，请检查地址或代理", code="MODEL_CATALOG_UNAVAILABLE") from None
        try:
            data = json.loads(chunks).get("data")
        except (ValueError, AttributeError):
            data = None
        if not isinstance(data, list) or len(data) > _MAX_CATALOG_ITEMS:
            raise SettingError(502, "供应商模型列表格式不受支持", code="MODEL_CATALOG_INVALID")
        items = []
        seen = set()
        for raw in data:
            if not isinstance(raw, dict):
                continue
            model_id = raw.get("id")
            if not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id) or model_id in seen:
                continue
            if request.provider == "opencode_console" and not (
                model_id in _OPENCODE_CHAT_EXACT
                or model_id.startswith(_OPENCODE_CHAT_PREFIXES)
            ):
                continue
            seen.add(model_id)
            name = raw.get("name")
            items.append(ModelCatalogItem(
                id=model_id,
                name=name[:120] if isinstance(name, str) and name.strip() else model_id,
            ))
        items.sort(key=lambda item: item.id.casefold())
        return ModelCatalogRead(items=items, total=len(items),
                                chat_only=request.provider != "custom")

    def _secret(self, request: ModelCatalogRequest, base: str) -> str | None:
        if request.secret is not None:
            if not request.secret.strip():
                raise SettingError(422, "API Key 不能为空", code="MODEL_CATALOG_SECRET_INVALID")
            return request.secret
        if request.model_id is None:
            return None
        setting = SettingMapper(self._db).get()
        automation = setting["value"].get("automation", {}) if setting else {}
        models = automation.get("models", []) if isinstance(automation, dict) else []
        saved = next((item for item in models if isinstance(item, dict)
                      and item.get("id") == request.model_id), None)
        params = saved.get("litellm_params", {}) if saved else {}
        if not isinstance(params, dict) or params.get("api_base", "").rstrip("/") != base \
                or (params.get("proxy_url") or None) != (request.proxy_url or None) \
                or bool(params.get("allow_insecure_http", False)) != request.allow_insecure_http:
            raise SettingError(
                422, "地址或代理已修改；请填写 API Key 或先保存配置后再获取列表",
                code="MODEL_CATALOG_UNSAVED_CONNECTION",
            )
        try:
            return self._secret_reader.get_for_provider(request.model_id)
        except ProtectedSecretStoreError:
            raise SettingError(503, "受保护凭据库不可用", code="PROTECTED_SECRET_STORE_ERROR") from None
