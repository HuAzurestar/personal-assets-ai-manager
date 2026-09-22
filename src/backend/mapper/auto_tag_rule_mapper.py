from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from backend.entity import (
    AMOUNT_MODE_BAND,
    AUTO_TAG_METHOD_LLM_DIRECT,
    AutoTagRule,
)
from backend.entity.auto_tag_rule import MAX_COUNTER_VALUE


METHOD_CONFIG_SCHEMA_VERSION = 1


def decode_method_config(method_config_json: str) -> dict[str, object]:
    try:
        value = json.loads(method_config_json)
    except json.JSONDecodeError as error:
        raise ValueError("method_config_json must be valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("method_config_json must be a JSON object")
    if value.get("schema_version") != METHOD_CONFIG_SCHEMA_VERSION:
        raise ValueError("method_config_json schema_version must be 1")
    model_id = value.get("model_id")
    if isinstance(model_id, bool) or not isinstance(model_id, int) or model_id <= 0:
        raise ValueError("method_config_json model_id must be a positive integer")
    if not isinstance(value.get("prompt"), str):
        raise ValueError("method_config_json prompt must be text")
    return value


def encode_method_config(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    decode_method_config(encoded)
    return encoded


class AutoTagRuleMapper:
    """Explicit-column persistence for automatic tag rules."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _columns():
        return (
            AutoTagRule.id,
            AutoTagRule.name,
            AutoTagRule.view_id,
            AutoTagRule.method,
            AutoTagRule.method_config_json,
            AutoTagRule.enabled,
            AutoTagRule.cron,
            AutoTagRule.amount_mode,
            AutoTagRule.rule_revision,
            AutoTagRule.scan_after_ledger_id,
            AutoTagRule.scan_epoch,
            AutoTagRule.analyzed_count,
            AutoTagRule.failed_count,
            AutoTagRule.suggested_count,
            AutoTagRule.accepted_count,
            AutoTagRule.rejected_count,
            AutoTagRule.created_time,
            AutoTagRule.updated_time,
        )

    @staticmethod
    def _decode_row(row) -> dict[str, object]:
        result = dict(row)
        result["method_config"] = decode_method_config(
            result.pop("method_config_json")
        )
        return result

    def begin_write(self) -> None:
        if self.db.bind is not None and self.db.bind.dialect.name == "sqlite":
            self.db.execute(text("BEGIN IMMEDIATE"))

    def get(self, rule_id: int) -> dict[str, object] | None:
        row = self.db.execute(select(*self._columns()).where(
            AutoTagRule.id == rule_id,
        )).mappings().one_or_none()
        return self._decode_row(row) if row is not None else None

    def for_view(self, view_id: int) -> list[dict[str, object]]:
        rows = self.db.execute(select(*self._columns()).where(
            AutoTagRule.view_id == view_id,
        ).order_by(AutoTagRule.id)).mappings().all()
        return [self._decode_row(row) for row in rows]

    def advance_for_model_ids(
        self,
        model_ids: set[int],
        *,
        now: datetime,
    ) -> datetime:
        """Invalidate rules whose effective model parameters changed."""

        if not model_ids:
            return now
        rows = self.db.execute(select(
            AutoTagRule.id,
            AutoTagRule.method_config_json,
            AutoTagRule.rule_revision,
            AutoTagRule.scan_epoch,
            AutoTagRule.updated_time,
        )).mappings().all()
        affected_ids: list[int] = []
        effective_time = now
        for row in rows:
            method_config = decode_method_config(row["method_config_json"])
            if method_config["model_id"] not in model_ids:
                continue
            if (
                row["rule_revision"] >= MAX_COUNTER_VALUE
                or row["scan_epoch"] >= MAX_COUNTER_VALUE
            ):
                raise ValueError("automatic tag rule revision counter is exhausted")
            affected_ids.append(row["id"])
            effective_time = max(
                effective_time,
                row["updated_time"] + timedelta(microseconds=1),
            )

        if not affected_ids:
            return now
        self.db.execute(
            update(AutoTagRule)
            .where(AutoTagRule.id.in_(affected_ids))
            .values(
                rule_revision=AutoTagRule.rule_revision + 1,
                scan_after_ledger_id=0,
                scan_epoch=AutoTagRule.scan_epoch + 1,
                updated_time=effective_time,
            )
        )
        return effective_time

    def create(
        self,
        *,
        name: str,
        view_id: int,
        method_config: dict[str, object],
        now: datetime,
        method: int = AUTO_TAG_METHOD_LLM_DIRECT,
        enabled: int = 0,
        cron: str = "",
        amount_mode: int = AMOUNT_MODE_BAND,
    ) -> int:
        rule = AutoTagRule(
            name=name,
            view_id=view_id,
            method=method,
            method_config_json=encode_method_config(method_config),
            enabled=enabled,
            cron=cron,
            amount_mode=amount_mode,
            rule_revision=1,
            scan_after_ledger_id=0,
            scan_epoch=1,
            analyzed_count=0,
            failed_count=0,
            suggested_count=0,
            accepted_count=0,
            rejected_count=0,
            created_time=now,
            updated_time=now,
        )
        self.db.add(rule)
        self.db.flush()
        return rule.id

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()
