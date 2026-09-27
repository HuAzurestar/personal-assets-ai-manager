"""Explicit one-request probe: no ledger reads, writes, retries or raw output."""
from datetime import datetime, timezone
from threading import Lock

from backend.error import SettingError
from backend.schema.setting import ModelConnectionCheckRead
from backend.service.llm_adapter import _direct_litellm_completion, _field, _provider_exception
from backend.service.setting_service import SettingService


_PROBE_LOCK = Lock()
MESSAGES = [{"role": "user", "content": "Reply with OK."}]
ERROR_MESSAGES = {
    "AUTH_ERROR": "凭据无效，请检查 API Key。",
    "CONFIG_ERROR": "地址或模型配置不被供应商接受。",
    "RATE_LIMIT": "供应商限流，请稍后再试。",
    "REQUEST_TIMEOUT": "连接检查超时，请稍后再试。",
    "PROVIDER_UNAVAILABLE": "无法连接供应商，请检查网络和地址。",
}


class ModelConnectionService:
    def __init__(self, db, secret_store, secret_reader, completion=None):
        self.db = db
        self.settings = SettingService(db, secret_store)
        self.secret_reader = secret_reader
        self.completion = completion or _direct_litellm_completion

    def check(self, model_id, expected_updated_time):
        if not _PROBE_LOCK.acquire(blocking=False):
            raise SettingError(409, "已有连接检查进行中，请稍后再试", code="MODEL_CHECK_BUSY")
        try:
            setting = self.settings.get_automation()
            if setting.updated_time != expected_updated_time:
                raise SettingError(409, "配置已改变，请刷新后检查", code="SETTING_VERSION_CONFLICT")
            model = next((m for m in setting.models if m.id == model_id), None)
            if model is None:
                raise SettingError(404, "模型不存在", code="MODEL_NOT_FOUND")
            # Release the read transaction before any credential/provider I/O.
            self.db.rollback()
            secret = self.secret_reader.get_for_provider(model_id)
            if not secret:
                raise SettingError(422, "请先保存 API Key", code="MODEL_SECRET_REQUIRED")
            params = model.litellm_params
            request = {
                "model": params.model, "api_base": params.api_base, "api_key": secret,
                "messages": [dict(m) for m in MESSAGES], "timeout": 15,
                "max_tokens": 32, "stream": False, "num_retries": 0, "max_retries": 0,
                "caching": False,
                "_lock_timeout": 0,
            }
            # Never forward arbitrary extensions capable of overriding probe limits.
            if isinstance((params.extra_body or {}).get("enable_thinking"), bool):
                request["extra_body"] = {"enable_thinking": params.extra_body["enable_thinking"]}
            connected, code, message = False, "INVALID_RESPONSE", "供应商未返回有效文本，请检查模型兼容性。"
            try:
                response = self.completion(**request)
                choices = _field(response, "choices")
                content = _field(_field(choices[0], "message"), "content") if choices else None
                if isinstance(content, str) and content.strip():
                    connected, code, message = True, "CONNECTED", "已收到真实模型响应。此检查不代表分类准确率。"
            except Exception as error:
                code = _provider_exception(error).code
                message = ERROR_MESSAGES.get(code, ERROR_MESSAGES["PROVIDER_UNAVAILABLE"])
            return ModelConnectionCheckRead(
                model_id=model_id, connected=connected, code=code, message=message,
                checked_at=datetime.now(timezone.utc),
                configuration_updated_time=setting.updated_time,
            )
        finally:
            _PROBE_LOCK.release()
