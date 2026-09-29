"""Register persisted automatic-tag rules in the shared scheduler."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy

from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.core.job_scheduler import JobOutcome, JobRunContext, JobScheduler
from backend.mapper.auto_tag_rule_mapper import AutoTagRuleMapper
from backend.mapper.auto_tag_scan_mapper import AutoTagScanMapper
from backend.mapper.setting_mapper import SettingMapper
from backend.service.auto_tag_scan_service import AutoTagScanService
from backend.service.configured_llm_analyzer import (
    ConfiguredLlmAnalyzer,
    ProviderSecretReader,
)
from backend.service.llm_privacy_service import LlmPrivacyService
from backend.service.setting_service import DEFAULT_DISCLOSURE


class AutoTagScheduleService:
    """Reconstruct and synchronize stable ``tag-scan:{rule_id}`` jobs."""

    def __init__(
        self,
        sessions: Callable[[], Session],
        scheduler: JobScheduler,
        secret_store: ProviderSecretReader,
        *,
        synthetic_acceptance_enabled: bool = False,
        real_analysis_enabled: bool = False,
    ):
        if synthetic_acceptance_enabled and real_analysis_enabled:
            raise ValueError("Real analysis and synthetic acceptance are exclusive")
        self._sessions = sessions
        self._scheduler = scheduler
        self._synthetic_acceptance_enabled = synthetic_acceptance_enabled
        self._real_analysis_enabled = real_analysis_enabled
        analyzer = ConfiguredLlmAnalyzer(sessions, secret_store)
        self._scan = AutoTagScanService(sessions, analyzer)

    def register_persisted(self) -> bool:
        if not (self._synthetic_acceptance_enabled or self._real_analysis_enabled):
            return True
        with self._sessions() as db:
            if not SettingMapper(db).scan_enabled():
                return True
            if not self._real_analysis_enabled and not (
                AutoTagScanMapper(db).is_synthetic_acceptance_database()
            ):
                return True
            schedules = AutoTagRuleMapper(db).enabled_schedules()
        results = [self._register(rule_id, expression) for rule_id, expression in schedules]
        return all(results)

    def sync_enabled(self) -> bool:
        """Apply a committed global setting without restarting the process."""
        for task in self._scheduler.snapshot().tasks:
            if task.task_key.startswith("tag-scan:"):
                try:
                    self._scheduler.remove(task.task_key)
                except KeyError:
                    pass
        return self.register_persisted()

    def sync_rule(self, rule_id: int) -> bool | None:
        if not (self._synthetic_acceptance_enabled or self._real_analysis_enabled):
            try:
                self._scheduler.remove(self.task_key(rule_id))
            except KeyError:
                pass
            return
        with self._sessions() as db:
            if not SettingMapper(db).scan_enabled():
                try:
                    self._scheduler.remove(self.task_key(rule_id))
                except KeyError:
                    pass
                return
            if not self._real_analysis_enabled and not (
                AutoTagScanMapper(db).is_synthetic_acceptance_database()
            ):
                try:
                    self._scheduler.remove(self.task_key(rule_id))
                except KeyError:
                    pass
                return
            rule = AutoTagRuleMapper(db).get(rule_id)
        task_key = self.task_key(rule_id)
        if rule is None or not bool(rule["enabled"]):
            try:
                self._scheduler.remove(task_key)
            except KeyError:
                pass
            return
        return self._register(rule_id, str(rule["cron"]))

    def _register(self, rule_id: int, expression: str) -> bool:
        try:
            self._scheduler.register_cron(
                self.task_key(rule_id), expression=expression, callback=self._callback(rule_id),
            )
        except Exception:
            self._scheduler.registration_failed(self.task_key(rule_id))
            return False
        return True

    def _callback(self, rule_id: int):
        async def run(context: JobRunContext) -> JobOutcome:
            with self._sessions() as db:
                if not SettingMapper(db).scan_enabled():
                    return JobOutcome("CANCELLED")
                if not self._real_analysis_enabled and not (
                    AutoTagScanMapper(db).is_synthetic_acceptance_database()
                ):
                    # Accepted Facts are immutable, so this database cannot become
                    # a synthetic-only fixture again. Keep the last failure visible
                    # while preventing every later CRON tick from repeating it.
                    try:
                        self._scheduler.pause(self.task_key(rule_id))
                    except KeyError:
                        pass
                    return JobOutcome("FAILED", "ACCEPTANCE_DATABASE_REQUIRED")
            try:
                privacy = self._privacy_service()
            except (ValidationError, ValueError, TypeError):
                return JobOutcome("FAILED", "CONFIG_ERROR")
            report = await self._scan.run_protected(
                rule_id,
                context,
                privacy,
                synthetic_only=not self._real_analysis_enabled,
            )
            if report.stopped_reason == "SOFT_BUDGET_EXHAUSTED":
                return JobOutcome("PARTIAL_FAILURE", report.stopped_reason)
            if report.failed_count:
                return JobOutcome(
                    result=(
                        "PARTIAL_FAILURE"
                        if report.successful_count > 0
                        else "FAILED"
                    ),
                    error_code=(
                        report.stopped_reason
                        if report.stopped_reason in {
                            "CONFIG_ERROR", "AUTH_ERROR", "AUDIT_STORAGE_ERROR", "COMMIT_FAILED", "COUNTER_EXHAUSTED",
                            "VIEW_INACTIVE", "NO_ACTIVE_TARGETS", "MODEL_DISABLED",
                        }
                        else report.last_error_code or "ITEM_FAILURE"
                    ),
                )
            if report.stopped_reason in {
                "RULE_NOT_FOUND", "RULE_DISABLED", "MODEL_DISABLED", "NO_ACTIVE_TARGETS",
                "CONFIG_ERROR", "AUTH_ERROR", "AUDIT_STORAGE_ERROR",
                "VIEW_INACTIVE", "COMMIT_FAILED", "COUNTER_EXHAUSTED",
                "SYNTHETIC_FIXTURE_MISSING", "SYNTHETIC_FIXTURE_INVALID",
            }:
                return JobOutcome("FAILED", report.stopped_reason)
            if report.stopped_reason in {
                "RULE_TOKEN_CHANGED", "CURSOR_ALREADY_ADVANCED",
            }:
                return JobOutcome("CANCELLED")
            outcome = report.stopped_reason
            if outcome == "PAGE_COMPLETE":
                outcome = (
                    "SUGGESTION" if report.request_count else
                    "INSUFFICIENT" if report.insufficient_count else
                    "NO_CALL" if report.no_call_count else "SKIPPED"
                )
            return JobOutcome("COMPLETED", outcome_code=outcome)

        return run

    def _privacy_service(self) -> LlmPrivacyService:
        with self._sessions() as db:
            setting = SettingMapper(db).get()
        disclosure = deepcopy(DEFAULT_DISCLOSURE)
        if setting is not None:
            value = setting["value"]
            automation = value.get("automation", {}) if isinstance(value, dict) else {}
            configured = (
                automation.get("disclosure")
                if isinstance(automation, dict)
                else None
            )
            if isinstance(configured, dict) and configured:
                disclosure = configured
        return LlmPrivacyService(disclosure)

    @staticmethod
    def task_key(rule_id: int) -> str:
        return f"tag-scan:{rule_id}"
