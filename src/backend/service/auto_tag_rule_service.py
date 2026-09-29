from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.core.job_scheduler import JobScheduler
from backend.entity import MAX_COUNTER_VALUE
from backend.entity.base import utc_now
from backend.error import AutoTagRuleError
from backend.mapper.auto_tag_rule_mapper import AutoTagRuleMapper
from backend.mapper.setting_mapper import SettingMapper
from backend.mapper.tag_assignment_request_mapper import TagAssignmentRequestMapper
from backend.mapper.target_tag_mapper import TargetTagMapper
from backend.schema.auto_tag_rule import (
    AutoTagCandidatePreviewRead,
    AutoTagCandidateSample,
    AutoTagRuleCreateRequest,
    AutoTagRuleListBody,
    AutoTagRuleListRequest,
    AutoTagRuleRead,
    AutoTagRuleSorter,
    AutoTagRuleSummaryRead,
    AutoTagRuleUpdateRequest,
    auto_tag_rule_filter,
)
from backend.schema.setting import AutomationModelWrite
from backend.schema.response import ResponseWarning


class AutoTagRuleService:
    """Manage automatic Tag rules without executing analysis work."""

    def __init__(
        self,
        db: Session,
        on_saved: Callable[[int], bool | None] | None = None,
    ):
        self.mapper = AutoTagRuleMapper(db)
        self.request_mapper = TagAssignmentRequestMapper(db)
        self.setting_mapper = SettingMapper(db)
        self.tag_mapper = TargetTagMapper(db)
        self._on_saved = on_saved
        self.warnings: list[ResponseWarning] = []

    def get(self, rule_id: int) -> AutoTagRuleRead:
        return self._read(self._required(rule_id))

    def list(self, request: AutoTagRuleListRequest) -> AutoTagRuleListBody:
        sorter_expression = request.sorter[0] if request.sorter else None
        sorter = AutoTagRuleSorter(
            field=sorter_expression.key if sorter_expression else "id",
            order=sorter_expression.direction if sorter_expression else "desc",
        )
        rows, total = self.mapper.list(
            page=request.page_index,
            page_size=request.page_size,
            request=request,
            filter_value=auto_tag_rule_filter(request),
            sorter=sorter,
        )
        return AutoTagRuleListBody(
            items=[self._read(row) for row in rows],
            total=total,
            page_index=request.page_index,
            page_size=request.page_size,
        )

    def create(self, payload: AutoTagRuleCreateRequest) -> AutoTagRuleRead:
        self._require_runnable_cron(payload.cron)
        try:
            self.mapper.begin_write()
            self._validate_references(
                view_id=payload.view_id,
                model_id=payload.method_config.model_id,
                enabled=payload.enabled,
            )
            rule_id = self.mapper.create(
                name=payload.name,
                view_id=payload.view_id,
                method=payload.method,
                method_config=payload.method_config.model_dump(mode="json"),
                enabled=int(payload.enabled),
                cron=payload.cron,
                amount_mode=payload.amount_mode,
                now=utc_now(),
            )
            self.mapper.commit()
            result = self._read(self._required(rule_id))
            self._after_saved(rule_id)
            return result
        except AutoTagRuleError:
            self.mapper.rollback()
            raise
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise AutoTagRuleError(
                409,
                "automatic tag rule write conflict; retry",
                code="AUTO_TAG_RULE_WRITE_CONFLICT",
            ) from error
        except Exception:
            self.mapper.rollback()
            raise

    def update(
        self,
        rule_id: int,
        payload: AutoTagRuleUpdateRequest,
    ) -> AutoTagRuleRead:
        try:
            self.mapper.begin_write()
            current = self._required(rule_id)
            self._require_current_version(
                payload.expected_updated_time,
                current["updated_time"],
            )
            self._require_runnable_cron(payload.cron)
            self._validate_references(
                view_id=int(current["view_id"]),
                model_id=payload.method_config.model_id,
                enabled=payload.enabled,
            )
            next_config = payload.method_config.model_dump(mode="json")
            semantic_changed = (
                int(current["method"]) != payload.method
                or self._json_signature(current["method_config"])
                != self._json_signature(next_config)
                or int(current["amount_mode"]) != payload.amount_mode
            )
            any_changed = semantic_changed or any((
                str(current["name"]) != payload.name,
                bool(current["enabled"]) != payload.enabled,
                str(current["cron"]) != payload.cron,
            ))
            if not any_changed:
                self.mapper.rollback()
                result = self._read(current)
                self._after_saved(rule_id)
                return result

            revision = int(current["rule_revision"])
            epoch = int(current["scan_epoch"])
            cursor = int(current["scan_after_ledger_id"])
            if semantic_changed:
                if revision >= MAX_COUNTER_VALUE or epoch >= MAX_COUNTER_VALUE:
                    raise AutoTagRuleError(
                        409,
                        "automatic tag rule revision counter is exhausted",
                        code="AUTO_TAG_RULE_REVISION_EXHAUSTED",
                    )
                revision += 1
                epoch += 1
                cursor = 0
            next_time = self._next_updated_time(current["updated_time"])
            if not self.mapper.update_fields(
                rule_id,
                name=payload.name,
                method=payload.method,
                method_config=next_config,
                enabled=payload.enabled,
                cron=payload.cron,
                amount_mode=payload.amount_mode,
                rule_revision=revision,
                scan_after_ledger_id=cursor,
                scan_epoch=epoch,
                now=next_time,
            ):
                raise AutoTagRuleError(404, "automatic tag rule not found")
            if semantic_changed:
                self.request_mapper.cancel_pending_for_rule(
                    rule_id,
                    before_revision=revision,
                    now=next_time,
                )
            self.mapper.commit()
            result = self._read(self._required(rule_id))
            self._after_saved(rule_id)
            return result
        except AutoTagRuleError:
            self.mapper.rollback()
            raise
        except (IntegrityError, OperationalError) as error:
            self.mapper.rollback()
            raise AutoTagRuleError(
                409,
                "automatic tag rule write conflict; retry",
                code="AUTO_TAG_RULE_WRITE_CONFLICT",
            ) from error
        except Exception:
            self.mapper.rollback()
            raise

    @staticmethod
    def _require_runnable_cron(expression: str) -> None:
        if not expression:
            return
        try:
            JobScheduler.preview_cron(expression)
        except ValueError as error:
            raise AutoTagRuleError(
                422,
                "cron expression has no future execution time",
                code="AUTO_TAG_RULE_CRON_NO_RUN",
            ) from error

    def _after_saved(self, rule_id: int) -> None:
        if self._on_saved is None:
            return
        try:
            registered = self._on_saved(rule_id)
        except Exception:
            registered = False
        if registered is False:
            self.warnings.append(ResponseWarning(
                code="REGISTER_FAILED",
                message="规则已保存，但调度注册失败；请在调度诊断中核对并重新保存。",
            ))

    def summary(self, rule_id: int) -> AutoTagRuleSummaryRead:
        rule = self._required(rule_id)
        analyzed = int(rule["analyzed_count"])
        successful = analyzed - int(rule["failed_count"])
        accepted = int(rule["accepted_count"])
        decisions = accepted + int(rule["rejected_count"])
        return AutoTagRuleSummaryRead(
            id=int(rule["id"]),
            rule_revision=int(rule["rule_revision"]),
            scan_after_ledger_id=int(rule["scan_after_ledger_id"]),
            scan_epoch=int(rule["scan_epoch"]),
            analyzed_count=str(rule["analyzed_count"]),
            failed_count=str(rule["failed_count"]),
            suggested_count=str(rule["suggested_count"]),
            accepted_count=str(rule["accepted_count"]),
            rejected_count=str(rule["rejected_count"]),
            execution_success_count=str(successful),
            execution_success_rate=successful / analyzed if analyzed else None,
            decision_count=str(decisions),
            acceptance_rate=accepted / decisions if decisions else None,
        )

    def candidate_preview(self, rule_id: int) -> AutoTagCandidatePreviewRead:
        rule = self._required(rule_id)
        view = self.tag_mapper.view(int(rule["view_id"]))
        model = self._model(int(rule["method_config"]["model_id"]))
        page = self.mapper.candidate_preview_page(
            rule_id=rule_id,
            rule_revision=int(rule["rule_revision"]),
            view_id=int(rule["view_id"]),
            after_id=int(rule["scan_after_ledger_id"]),
        )
        ledger_ids = page["ledger_ids"]
        active_ids = page["active_ids"]
        tag_states = page["tag_states"]
        request_ids = page["request_ids"]
        reason_counts: dict[str, int] = {}
        sample_ids: list[int] = []

        global_reason = None
        if not bool(rule["enabled"]):
            global_reason = "RULE_DISABLED"
        elif view.status != "ACTIVE":
            global_reason = "VIEW_INACTIVE"
        elif not model.enabled:
            global_reason = "MODEL_DISABLED"
        elif int(page["active_target_count"]) == 0:
            global_reason = "NO_ACTIVE_TARGET_TAG"

        for ledger_id in ledger_ids:
            if global_reason is not None:
                reason = global_reason
            elif ledger_id not in active_ids:
                reason = "INACTIVE_LEDGER"
            else:
                states = tag_states.get(ledger_id, [])
                if len(states) != 1 or states[0][1] != "ACTIVE":
                    reason = "MISSING_VIEW_TAG"
                elif states[0][0] != "unclassified":
                    reason = "ALREADY_CLASSIFIED"
                elif ledger_id in request_ids:
                    reason = "EXISTING_REQUEST"
                else:
                    reason = "ELIGIBLE"
                    if len(sample_ids) < 20:
                        sample_ids.append(ledger_id)
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

        sample_details = self.mapper.candidate_sample_details(sample_ids)
        return AutoTagCandidatePreviewRead(
            rule_id=rule_id,
            mode="SIMULATED_LOCAL",
            inspected_count=len(ledger_ids),
            eligible_count=reason_counts.get("ELIGIBLE", 0),
            reason_counts=reason_counts,
            samples=[
                AutoTagCandidateSample(reason="ELIGIBLE", **sample_details[ledger_id])
                for ledger_id in sample_ids if ledger_id in sample_details
            ],
            scan_after_ledger_id=int(rule["scan_after_ledger_id"]),
            page_limit=100,
            sample_limit=20,
            message="仅预览本地候选资格，未调用模型或更新扫描进度",
        )

    def _required(self, rule_id: int) -> dict[str, object]:
        try:
            rule = self.mapper.get(rule_id)
        except (TypeError, ValueError) as error:
            raise AutoTagRuleError(
                500,
                "stored automatic tag rule is invalid",
                code="AUTO_TAG_RULE_DATA_INVALID",
            ) from error
        if rule is None:
            raise AutoTagRuleError(
                404,
                "automatic tag rule not found",
                code="AUTO_TAG_RULE_NOT_FOUND",
            )
        return rule

    def _validate_references(
        self,
        *,
        view_id: int,
        model_id: int,
        enabled: bool,
    ) -> None:
        view = self.tag_mapper.view(view_id)
        if view is None:
            raise AutoTagRuleError(
                422,
                "tag view not found",
                code="AUTO_TAG_RULE_VIEW_INVALID",
            )
        model = self._model(model_id)
        if enabled and view.status != "ACTIVE":
            raise AutoTagRuleError(
                422,
                "enabled rules require an active tag view",
                code="AUTO_TAG_RULE_VIEW_INACTIVE",
            )
        if enabled and not model.enabled:
            raise AutoTagRuleError(
                422,
                "enabled rules require an enabled model",
                code="AUTO_TAG_RULE_MODEL_DISABLED",
            )

    def _model(self, model_id: int) -> AutomationModelWrite:
        try:
            setting = self.setting_mapper.get()
        except (TypeError, ValueError) as error:
            raise AutoTagRuleError(
                500,
                "stored model setting is invalid",
                code="AUTO_TAG_RULE_MODEL_DATA_INVALID",
            ) from error
        if setting is None:
            raise AutoTagRuleError(
                422,
                "model not found",
                code="AUTO_TAG_RULE_MODEL_INVALID",
            )
        value = setting["value"]
        automation = value.get("automation", {})
        raw_models = automation.get("models", []) if isinstance(automation, dict) else []
        try:
            models = [AutomationModelWrite.model_validate(item) for item in raw_models]
        except ValidationError as error:
            raise AutoTagRuleError(
                500,
                "stored model setting is invalid",
                code="AUTO_TAG_RULE_MODEL_DATA_INVALID",
            ) from error
        model = next((item for item in models if item.id == model_id), None)
        if model is None:
            raise AutoTagRuleError(
                422,
                "model not found",
                code="AUTO_TAG_RULE_MODEL_INVALID",
            )
        return model

    @staticmethod
    def _read(row: dict[str, object]) -> AutoTagRuleRead:
        return AutoTagRuleRead(
            id=int(row["id"]),
            name=str(row["name"]),
            view_id=int(row["view_id"]),
            method=int(row["method"]),
            method_config=row["method_config"],
            enabled=bool(row["enabled"]),
            cron=str(row["cron"]),
            amount_mode=int(row["amount_mode"]),
            rule_revision=int(row["rule_revision"]),
            scan_after_ledger_id=int(row["scan_after_ledger_id"]),
            scan_epoch=int(row["scan_epoch"]),
            analyzed_count=str(row["analyzed_count"]),
            failed_count=str(row["failed_count"]),
            suggested_count=str(row["suggested_count"]),
            accepted_count=str(row["accepted_count"]),
            rejected_count=str(row["rejected_count"]),
            created_time=row["created_time"],
            updated_time=row["updated_time"],
        )

    @staticmethod
    def _json_signature(value: object) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _require_current_version(expected: datetime, current: object) -> None:
        if not isinstance(current, datetime):
            raise AutoTagRuleError(
                500,
                "stored automatic tag rule timestamp is invalid",
                code="AUTO_TAG_RULE_DATA_INVALID",
            )
        if expected.astimezone(timezone.utc) != current.astimezone(timezone.utc):
            raise AutoTagRuleError(
                409,
                "automatic tag rule changed; refresh and retry",
                code="AUTO_TAG_RULE_VERSION_CONFLICT",
                details={"current_updated_time": current.isoformat()},
            )

    @staticmethod
    def _next_updated_time(current: object) -> datetime:
        if not isinstance(current, datetime):
            raise AutoTagRuleError(
                500,
                "stored automatic tag rule timestamp is invalid",
                code="AUTO_TAG_RULE_DATA_INVALID",
            )
        return max(utc_now(), current + timedelta(microseconds=1))
