"""Register persisted automatic-tag rules in the shared scheduler."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy

from sqlalchemy.orm import Session

from backend.core.job_scheduler import JobRunContext, JobScheduler
from backend.mapper.auto_tag_rule_mapper import AutoTagRuleMapper
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
    ):
        self._sessions = sessions
        self._scheduler = scheduler
        analyzer = ConfiguredLlmAnalyzer(sessions, secret_store)
        self._scan = AutoTagScanService(sessions, analyzer)

    def register_persisted(self) -> None:
        with self._sessions() as db:
            schedules = AutoTagRuleMapper(db).enabled_schedules()
        for rule_id, expression in schedules:
            self._register(rule_id, expression)

    def sync_rule(self, rule_id: int) -> None:
        with self._sessions() as db:
            rule = AutoTagRuleMapper(db).get(rule_id)
        task_key = self.task_key(rule_id)
        if rule is None or not bool(rule["enabled"]):
            try:
                self._scheduler.remove(task_key)
            except KeyError:
                pass
            return
        self._register(rule_id, str(rule["cron"]))

    def _register(self, rule_id: int, expression: str) -> None:
        self._scheduler.register_cron(
            self.task_key(rule_id),
            expression=expression,
            callback=self._callback(rule_id),
        )

    def _callback(self, rule_id: int):
        async def run(context: JobRunContext) -> None:
            await self._scan.run_protected(
                rule_id,
                context,
                self._privacy_service(),
            )

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
