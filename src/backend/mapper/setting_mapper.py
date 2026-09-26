from __future__ import annotations

import json
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.entity import Setting
from backend.schema.setting import AutomationDisclosure, AutomationModelWrite


SETTING_SCHEMA_VERSION = 1


def decode_setting_value(value_json: str) -> dict[str, object]:
    try:
        value = json.loads(value_json)
    except json.JSONDecodeError as error:
        raise ValueError("setting value_json must be valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("setting value_json must be a JSON object")
    if value.get("schema_version") != SETTING_SCHEMA_VERSION:
        raise ValueError("setting schema_version must be 1")
    automation = value.get("automation")
    if automation is not None and not isinstance(automation, dict):
        raise ValueError("setting automation must be a JSON object")
    if isinstance(automation, dict):
        models = automation.get("models", [])
        disclosure = automation.get("disclosure", {})
        if not isinstance(models, list):
            raise ValueError("setting automation models must be a JSON array")
        if not isinstance(disclosure, dict):
            raise ValueError("setting automation disclosure must be a JSON object")
        try:
            AutomationDisclosure.model_validate(disclosure)
            parsed_models = [
                AutomationModelWrite.model_validate(model) for model in models
            ]
        except ValidationError as error:
            raise ValueError("setting automation configuration is invalid") from error
        model_ids = [model.id for model in parsed_models]
        if len(set(model_ids)) != len(model_ids):
            raise ValueError("setting automation model ids must be unique")
    return value


def encode_setting_value(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    decode_setting_value(encoded)
    return encoded


class SettingMapper:
    """Explicit-column persistence for the singleton application setting."""

    def __init__(self, db: Session):
        self.db = db

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    def get(self) -> dict[str, object] | None:
        row = self.db.execute(select(
            Setting.id,
            Setting.value_json,
            Setting.created_time,
            Setting.updated_time,
        ).where(Setting.id == 1)).mappings().one_or_none()
        if row is None:
            return None
        result = dict(row)
        result["value"] = decode_setting_value(result.pop("value_json"))
        return result

    def save(self, value: dict[str, object], now: datetime) -> None:
        value_json = encode_setting_value(value)
        setting = self.db.get(Setting, 1)
        if setting is None:
            setting = Setting(
                id=1,
                value_json=value_json,
                created_time=now,
                updated_time=now,
            )
            self.db.add(setting)
        else:
            setting.value_json = value_json
            setting.updated_time = now
        self.db.flush()

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()
