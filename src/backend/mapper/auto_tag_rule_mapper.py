from __future__ import annotations

import json
from datetime import datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import case, distinct, func, select, text, update
from sqlalchemy.orm import Session

from backend.entity import (
    AMOUNT_MODE_BAND,
    AUTO_TAG_METHOD_LLM_DIRECT,
    AutoTagRule,
    LedgerEntry,
    LedgerEntryTag,
    ReviewAllocation,
    ReviewCase,
    TagAssignmentRequest,
    TargetTag,
)
from backend.entity.auto_tag_rule import MAX_COUNTER_VALUE
from backend.schema.auto_tag_rule import (
    AutoTagMethodConfig,
    AutoTagRuleFilter,
    AutoTagRuleListRequest,
    AutoTagRuleSorter,
)


def decode_method_config(method_config_json: str) -> dict[str, object]:
    try:
        value = json.loads(method_config_json)
    except json.JSONDecodeError as error:
        raise ValueError("method_config_json must be valid JSON") from error
    if not isinstance(value, dict):
        raise ValueError("method_config_json must be a JSON object")
    try:
        validated = AutoTagMethodConfig.model_validate(value).model_dump(mode="json")
    except ValidationError as error:
        raise ValueError("method_config_json does not match schema version 1") from error
    if validated != value:
        raise ValueError("method_config_json must use canonical schema values")
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

    def list(
        self,
        *,
        page: int,
        page_size: int,
        request: AutoTagRuleListRequest,
        filter_value: AutoTagRuleFilter,
        sorter: AutoTagRuleSorter,
    ) -> tuple[list[dict[str, object]], int]:
        clauses = []
        if filter_value.id is not None:
            clauses.append(AutoTagRule.id == filter_value.id)
        if filter_value.view_id is not None:
            clauses.append(AutoTagRule.view_id == filter_value.view_id)
        if filter_value.enabled is not None:
            clauses.append(AutoTagRule.enabled == int(filter_value.enabled))
        if filter_value.method is not None:
            clauses.append(AutoTagRule.method == filter_value.method)
        if filter_value.amount_mode is not None:
            clauses.append(AutoTagRule.amount_mode == filter_value.amount_mode)
        if filter_value.created_time_start is not None:
            clauses.append(AutoTagRule.created_time >= filter_value.created_time_start)
        if filter_value.created_time_end is not None:
            clauses.append(AutoTagRule.created_time < filter_value.created_time_end)
        if filter_value.updated_time_start is not None:
            clauses.append(AutoTagRule.updated_time >= filter_value.updated_time_start)
        if filter_value.updated_time_end is not None:
            clauses.append(AutoTagRule.updated_time < filter_value.updated_time_end)
        clauses.extend(
            AutoTagRule.name.contains(expression.word)
            for expression in request.query
        )
        total = int(self.db.scalar(
            select(func.count(AutoTagRule.id)).where(*clauses)
        ) or 0)
        sort_columns = {
            "id": AutoTagRule.id,
            "created_time": AutoTagRule.created_time,
            "updated_time": AutoTagRule.updated_time,
        }
        column = sort_columns[sorter.field]
        order = column.asc() if sorter.order == "asc" else column.desc()
        id_order = AutoTagRule.id.asc() if sorter.order == "asc" else AutoTagRule.id.desc()
        rows = self.db.execute(select(*self._columns()).where(
            *clauses,
        ).order_by(order, id_order).offset(
            (page - 1) * page_size,
        ).limit(page_size)).mappings().all()
        return [self._decode_row(row) for row in rows], total

    def update_fields(
        self,
        rule_id: int,
        *,
        name: str,
        method: int,
        method_config: dict[str, object],
        enabled: bool,
        cron: str,
        amount_mode: int,
        rule_revision: int,
        scan_after_ledger_id: int,
        scan_epoch: int,
        now: datetime,
    ) -> bool:
        result = self.db.execute(
            update(AutoTagRule)
            .where(AutoTagRule.id == rule_id)
            .values(
                name=name,
                method=method,
                method_config_json=encode_method_config(method_config),
                enabled=int(enabled),
                cron=cron,
                amount_mode=amount_mode,
                rule_revision=rule_revision,
                scan_after_ledger_id=scan_after_ledger_id,
                scan_epoch=scan_epoch,
                updated_time=now,
            )
        )
        self.db.flush()
        return result.rowcount == 1

    def candidate_preview_page(
        self,
        *,
        rule_id: int,
        rule_revision: int,
        view_id: int,
        after_id: int,
        limit: int = 100,
    ) -> dict[str, object]:
        ledger_ids = list(self.db.scalars(select(
            LedgerEntry.id,
        ).where(
            LedgerEntry.id > after_id,
        ).order_by(LedgerEntry.id).limit(limit)).all())
        if not ledger_ids:
            return {
                "ledger_ids": [],
                "active_ids": set(),
                "tag_states": {},
                "request_ids": set(),
                "active_target_count": 0,
            }

        active_ids = set(self.db.scalars(select(
            distinct(ReviewAllocation.ledger_entry_id),
        ).join(
            ReviewCase,
            ReviewCase.id == ReviewAllocation.review_case_id,
        ).where(
            ReviewAllocation.ledger_entry_id.in_(ledger_ids),
            ReviewCase.status == 0,
        )).all())
        tag_rows = self.db.execute(select(
            LedgerEntryTag.ledger_id,
            TargetTag.system_name,
            TargetTag.status,
        ).join(
            TargetTag,
            TargetTag.id == LedgerEntryTag.tag_id,
        ).where(
            LedgerEntryTag.ledger_id.in_(ledger_ids),
            TargetTag.view_id == view_id,
        ).order_by(
            LedgerEntryTag.ledger_id,
            TargetTag.id,
        )).mappings().all()
        tag_states: dict[int, list[tuple[str, str]]] = {}
        for row in tag_rows:
            tag_states.setdefault(row["ledger_id"], []).append(
                (row["system_name"], row["status"])
            )
        request_ids = set(self.db.scalars(select(
            distinct(TagAssignmentRequest.ledger_id),
        ).where(
            TagAssignmentRequest.rule_id == rule_id,
            TagAssignmentRequest.rule_revision == rule_revision,
            TagAssignmentRequest.ledger_id.in_(ledger_ids),
        )).all())
        active_target_count = int(self.db.scalar(select(
            func.count(TargetTag.id),
        ).where(
            TargetTag.view_id == view_id,
            TargetTag.status == "ACTIVE",
            TargetTag.system_name != "unclassified",
        )) or 0)
        return {
            "ledger_ids": ledger_ids,
            "active_ids": active_ids,
            "tag_states": tag_states,
            "request_ids": request_ids,
            "active_target_count": active_target_count,
        }

    def advance_for_model_ids(
        self,
        model_ids: set[int],
        *,
        now: datetime,
    ) -> tuple[datetime, list[int]]:
        """Invalidate rules whose effective model parameters changed."""

        if not model_ids:
            return now, []
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
            return now, []
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
        return effective_time, affected_ids

    def advance_for_view_ids(
        self,
        view_ids: set[int],
        *,
        now: datetime,
    ) -> tuple[datetime, list[int]]:
        """Invalidate rules affected by an effective Tag dictionary change."""

        if not view_ids:
            return now, []
        rows = self.db.execute(select(
            AutoTagRule.id,
            AutoTagRule.rule_revision,
            AutoTagRule.scan_epoch,
            AutoTagRule.updated_time,
        ).where(
            AutoTagRule.view_id.in_(view_ids),
        )).mappings().all()
        if not rows:
            return now, []
        if any(
            row["rule_revision"] >= MAX_COUNTER_VALUE
            or row["scan_epoch"] >= MAX_COUNTER_VALUE
            for row in rows
        ):
            raise ValueError("automatic tag rule revision counter is exhausted")
        effective_time = max(
            now,
            *(row["updated_time"] + timedelta(microseconds=1) for row in rows),
        )
        affected_ids = [row["id"] for row in rows]
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
        return effective_time, affected_ids

    def rewind_for_ledger_ids(
        self,
        ledger_ids: list[int],
        *,
        now: datetime,
        view_ids: set[int] | None = None,
    ) -> tuple[datetime, list[int]]:
        """Invalidate in-flight scans and rewind past affected Ledger IDs."""

        if not ledger_ids:
            return now, []
        if view_ids == set():
            return now, []
        minimum_id = min(ledger_ids)
        clauses = [] if view_ids is None else [AutoTagRule.view_id.in_(view_ids)]
        rewind_to = max(0, minimum_id - 1)
        affected_ids = list(self.db.scalars(
            update(AutoTagRule)
            .where(*clauses)
            .values(
                scan_after_ledger_id=case(
                    (
                        AutoTagRule.scan_after_ledger_id > rewind_to,
                        rewind_to,
                    ),
                    else_=AutoTagRule.scan_after_ledger_id,
                ),
                scan_epoch=AutoTagRule.scan_epoch + 1,
                updated_time=now,
            )
            .returning(AutoTagRule.id)
        ).all())
        return now, affected_ids

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
