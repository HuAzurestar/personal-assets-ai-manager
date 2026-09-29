from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.core import ProtectedSecretStore
from backend.core.config import AUTOTAG_REAL_ANALYSIS, AUTOTAG_SYNTHETIC_ACCEPTANCE
from backend.entity.base import utc_now
from backend.error import SettingError
from backend.mapper.auto_tag_rule_mapper import AutoTagRuleMapper
from backend.mapper.setting_mapper import SETTING_SCHEMA_VERSION, SettingMapper
from backend.mapper.tag_assignment_request_mapper import TagAssignmentRequestMapper
from backend.schema.setting import (
    AutomationDisclosure,
    AutomationModelRead,
    AutomationModelWrite,
    AutomationSettingRead,
    AutomationSettingUpdateRequest,
    ModelConnectionTestRead,
    ModelSecretStateRead,
)
from backend.schema.response import ResponseWarning


DEFAULT_DISCLOSURE: dict[str, Any] = {
    "date_granularity": "DAY",
    "amount_bands": {
        "CNY": {"boundaries": [0, 3000, 10000, 50000, 300000]},
    },
}


class SettingService:
    """Persist model profiles separately from their protected credentials."""

    def __init__(self, db: Session, secret_store: ProtectedSecretStore,
                 on_scan_setting_changed: Callable[[], bool | None] | None = None):
        self.mapper = SettingMapper(db)
        self.rule_mapper = AutoTagRuleMapper(db)
        self.request_mapper = TagAssignmentRequestMapper(db)
        self.secret_store = secret_store
        self.on_scan_setting_changed = on_scan_setting_changed
        self.warnings: list[ResponseWarning] = []

    def get_automation(self) -> AutomationSettingRead:
        setting = self._load_setting()
        value, updated_time = self._value_and_time(setting)
        return self._automation_read(value, updated_time)

    def update_automation(
        self,
        payload: AutomationSettingUpdateRequest,
    ) -> AutomationSettingRead:
        key_states = None if payload.models is None else {
            model.id: self.secret_store.is_configured(model.id)
            for model in payload.models
        }
        missing_enabled_ids = sorted(
            model.id
            for model in payload.models or []
            if model.enabled and not key_states[model.id]
        )
        if missing_enabled_ids:
            raise SettingError(
                422,
                "enabled models require a configured secret",
                code="MODEL_KEY_REQUIRED",
                details={"model_ids": missing_enabled_ids},
            )

        try:
            self.mapper.begin_write()
            setting = self._load_setting()
            value, current_time = self._value_and_time(setting)
            self._require_current_version(payload.expected_updated_time, current_time)

            current_models = self._models_from_value(value)
            current_by_id = {model.id: model for model in current_models}
            next_models = current_models if payload.models is None else payload.models
            new_by_id = {model.id: model for model in next_models}
            removed_ids = sorted(set(current_by_id) - set(new_by_id))
            if removed_ids:
                raise SettingError(
                    422,
                    "model ids cannot be removed or reused; disable the model instead",
                    code="MODEL_REMOVAL_NOT_SUPPORTED",
                    details={"model_ids": removed_ids},
                )

            changed_parameter_ids = {
                model_id
                for model_id in set(current_by_id) & set(new_by_id)
                if self._parameter_signature(current_by_id[model_id])
                != self._parameter_signature(new_by_id[model_id])
            }
            next_time = self._next_updated_time(current_time)
            next_value = deepcopy(value)
            automation = next_value.setdefault("automation", {})
            if not isinstance(automation, dict):
                raise SettingError(
                    500,
                    "stored automation setting is invalid",
                    code="SETTING_DATA_INVALID",
                )
            current_disclosure = AutomationDisclosure.model_validate(
                automation.get("disclosure", {}),
            )
            current_scan_enabled = automation.get("scan_enabled", True)
            if payload.scan_enabled is not None:
                automation["scan_enabled"] = payload.scan_enabled
            next_disclosure = payload.disclosure or current_disclosure
            if payload.disclosure is not None:
                automation["disclosure"] = next_disclosure.model_dump(mode="json")
            if payload.models is not None:
                automation["models"] = [
                    model.model_dump(mode="json", exclude_unset=True)
                    for model in next_models
                ]
            next_time, affected_rule_ids = self.rule_mapper.advance_for_configuration(
                changed_parameter_ids,
                now=next_time,
                amount_bands_changed=(
                    current_disclosure.amount_bands != next_disclosure.amount_bands
                ),
                date_granularity_changed=(
                    current_disclosure.date_granularity != next_disclosure.date_granularity
                ),
            )
            self.request_mapper.cancel_pending_for_rule_ids(
                affected_rule_ids,
                now=next_time,
            )
            self.mapper.save(next_value, next_time)
            self.mapper.commit()
            if payload.scan_enabled is not None and payload.scan_enabled != current_scan_enabled:
                if self.on_scan_setting_changed is not None:
                    try:
                        synchronized = self.on_scan_setting_changed()
                    except Exception:
                        synchronized = False
                    if synchronized is False:
                        self.warnings.append(ResponseWarning(
                            code="SCAN_SYNC_FAILED",
                            message="自动分析设置已保存，但调度同步失败；请检查诊断或重启服务。",
                        ))
            return self._automation_read(
                next_value,
                next_time,
                key_states=key_states,
            )
        except SettingError:
            self.mapper.rollback()
            raise
        except ValueError as error:
            self.mapper.rollback()
            raise SettingError(
                409,
                "automatic tag rules cannot be invalidated safely",
                code="RULE_REVISION_CONFLICT",
            ) from error
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise SettingError(
                409,
                "setting write conflict; retry",
                code="SETTING_WRITE_CONFLICT",
            ) from error
        except Exception:
            self.mapper.rollback()
            raise

    def update_model_secret(
        self,
        model_id: int,
        secret: str,
    ) -> ModelSecretStateRead:
        self._require_model(model_id)
        self.secret_store.set(model_id, secret)
        return ModelSecretStateRead(model_id=model_id, key_configured=True)

    def delete_model_secret(self, model_id: int) -> ModelSecretStateRead:
        self._require_model(model_id)
        self.secret_store.delete(model_id)
        return ModelSecretStateRead(model_id=model_id, key_configured=False)

    def test_model_connection(self, model_id: int) -> ModelConnectionTestRead:
        self._require_model(model_id)
        configured = self.secret_store.is_configured(model_id)
        return ModelConnectionTestRead(
            model_id=model_id,
            connected=False,
            mode="SIMULATED",
            key_configured=configured,
            message="仅检查本地连接配置，未向模型供应商发送请求",
        )

    def _require_model(self, model_id: int) -> AutomationModelWrite:
        value, _ = self._value_and_time(self._load_setting())
        models = self._models_from_value(value)
        model = next((item for item in models if item.id == model_id), None)
        if model is None:
            raise SettingError(404, "model not found", code="MODEL_NOT_FOUND")
        return model

    def _load_setting(self) -> dict[str, object] | None:
        try:
            return self.mapper.get()
        except (TypeError, ValueError) as error:
            raise SettingError(
                500,
                "stored setting is invalid",
                code="SETTING_DATA_INVALID",
            ) from error

    @staticmethod
    def _value_and_time(
        setting: dict[str, object] | None,
    ) -> tuple[dict[str, object], datetime | None]:
        if setting is None:
            return {
                "schema_version": SETTING_SCHEMA_VERSION,
                "automation": {
                    "models": [],
                    "disclosure": deepcopy(DEFAULT_DISCLOSURE),
                },
            }, None
        value = setting["value"]
        updated_time = setting["updated_time"]
        if not isinstance(value, dict) or not isinstance(updated_time, datetime):
            raise SettingError(
                500,
                "stored setting is invalid",
                code="SETTING_DATA_INVALID",
            )
        return value, updated_time

    @staticmethod
    def _models_from_value(
        value: dict[str, object],
    ) -> list[AutomationModelWrite]:
        automation = value.get("automation", {})
        if not isinstance(automation, dict):
            raise SettingError(
                500,
                "stored automation setting is invalid",
                code="SETTING_DATA_INVALID",
            )
        raw_models = automation.get("models", [])
        if not isinstance(raw_models, list):
            raise SettingError(
                500,
                "stored model setting is invalid",
                code="SETTING_DATA_INVALID",
            )
        try:
            models = [AutomationModelWrite.model_validate(item) for item in raw_models]
        except ValidationError as error:
            raise SettingError(
                500,
                "stored model setting is invalid",
                code="SETTING_DATA_INVALID",
            ) from error
        model_ids = [model.id for model in models]
        if len(set(model_ids)) != len(model_ids):
            raise SettingError(
                500,
                "stored model ids are not unique",
                code="SETTING_DATA_INVALID",
            )
        return models

    def _automation_read(
        self,
        value: dict[str, object],
        updated_time: datetime | None,
        *,
        key_states: dict[int, bool] | None = None,
    ) -> AutomationSettingRead:
        automation = value.get("automation", {})
        if not isinstance(automation, dict):
            raise SettingError(
                500,
                "stored automation setting is invalid",
                code="SETTING_DATA_INVALID",
            )
        disclosure = automation.get("disclosure", {})
        if not isinstance(disclosure, dict):
            raise SettingError(
                500,
                "stored disclosure setting is invalid",
                code="SETTING_DATA_INVALID",
            )
        parsed_models = self._models_from_value(value)
        if key_states is None:
            key_states = {
                model.id: self.secret_store.is_configured(model.id)
                for model in parsed_models
            }
        models = [
            AutomationModelRead(
                **model.model_dump(mode="python", exclude_unset=True),
                key_configured=key_states[model.id],
            )
            for model in parsed_models
        ]
        return AutomationSettingRead(
            models=models,
            disclosure=AutomationDisclosure.model_validate(disclosure),
            scan_enabled=automation.get("scan_enabled", True),
            scan_available=AUTOTAG_REAL_ANALYSIS or AUTOTAG_SYNTHETIC_ACCEPTANCE,
            updated_time=updated_time,
        )

    @staticmethod
    def _require_current_version(
        expected: datetime | None,
        current: datetime | None,
    ) -> None:
        expected_utc = expected.astimezone(timezone.utc) if expected is not None else None
        current_utc = current.astimezone(timezone.utc) if current is not None else None
        if expected_utc != current_utc:
            raise SettingError(
                409,
                "setting changed; refresh and retry",
                code="SETTING_VERSION_CONFLICT",
                details={
                    "current_updated_time": (
                        current_utc.isoformat() if current_utc is not None else None
                    )
                },
            )

    @staticmethod
    def _next_updated_time(current: datetime | None) -> datetime:
        now = utc_now()
        if current is None:
            return now
        return max(now, current + timedelta(microseconds=1))

    @staticmethod
    def _parameter_signature(model: AutomationModelWrite) -> str:
        # Compare what the adapter sends, not whether a default was explicitly
        # present in storage. GET -> PUT adds nullable defaults to sparse models.
        params = model.litellm_params.model_dump(mode="json", exclude_none=True)
        params["timeout"] = model.litellm_params.timeout or 60.0
        return json.dumps(
            params,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
