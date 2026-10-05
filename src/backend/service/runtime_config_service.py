"""Compose the scheduler configuration from the existing singleton JSON."""
from __future__ import annotations

import os
import hashlib
from middleware.llm.contract import canonical
from dataclasses import asdict
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from backend.mapper.setting_mapper import SettingMapper
from backend.mapper.auto_tag_rule_mapper import AutoTagRuleMapper
from backend.core.job_scheduler import JobScheduler
from middleware.config import ConfigApplier, ConfigDefinition, ConfigResolver


def scan_control_definition():
    return ConfigDefinition("scan-control", bool, "true", "tag-scheduler",
                            sources=("deployment", "persisted", "default"),
                            env_key="PAAM_SCAN_ENABLED", failure_policy="disable")


def resolve_scan_enabled(saved):
    resolver = ConfigResolver()
    resolver.register(scan_control_definition())
    return resolver.resolve("scan-control", {"scan-control": saved}, None, dict(os.environ)).value


class _ScheduleValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    enabled: int
    cron: str


class _Storage:
    def __init__(self, sessions):
        self.sessions = sessions

    def read(self):
        with self.sessions() as db:
            if db.bind is not None and db.bind.dialect.name == "sqlite":
                db.execute(text("BEGIN"))
            setting = SettingMapper(db).get()
            schedules = AutoTagRuleMapper(db).config_schedules()
        values = {"tag-schedule": schedules}
        token = None
        if setting:
            values["scan-control"] = setting["value"].get("automation", {}).get("scan_enabled", True)
            token = setting["updated_time"].isoformat()
        # Schedules do not live in the singleton: bind the storage token to the
        # complete short-read basis without changing effective section values.
        return values, hashlib.sha256(canonical([token, schedules]).encode()).hexdigest()


class _ScheduleInstaller:
    def __init__(self, schedule):
        self.schedule = schedule

    def prepare(self, snapshots):
        values = {s.section: s.value for s in snapshots}
        for row in values["tag-schedule"]:
            if row["enabled"]:
                JobScheduler.preview_cron(row["cron"])
        return values

    def discard(self, candidate):
        pass

    def install(self, candidate):
        scheduler = self.schedule._scheduler
        # The existing engine is the single publication boundary. If installing
        # a timer fails, disable this scope before exposing the partial registry.
        with scheduler.configuration_guard():
            enabled = candidate["scan-control"] and (self.schedule._real_analysis_enabled or self.schedule._synthetic_acceptance_enabled)
            self.schedule.effective_enabled = enabled
            wanted = {self.schedule.task_key(row["id"]): row for row in candidate["tag-schedule"]}
            existing = {t.task_key for t in scheduler.snapshot().tasks if t.task_key.startswith("tag-scan:")}
            for key in existing - wanted.keys():
                scheduler.remove(key)
            for key, row in wanted.items():
                if not row["enabled"] or not enabled:
                    if key in existing:
                        scheduler.pause(key)
                    continue
                if not self.schedule._register(row["id"], row["cron"]):
                    self.disable()
                    raise RuntimeError("Schedule resource unavailable")

    def disable(self):
        self.schedule.effective_enabled = False
        for task in self.schedule._scheduler.snapshot().tasks:
            if task.task_key.startswith("tag-scan:"):
                try:
                    self.schedule._scheduler.pause(task.task_key)
                except KeyError:
                    pass


class RuntimeConfigService:
    def __init__(self, sessions, schedule):
        self.schedule = schedule
        self.resolver = ConfigResolver()
        self.resolver.register(scan_control_definition())
        self.resolver.register(ConfigDefinition("tag-schedule", list[_ScheduleValue], "[]", "tag-scheduler",
                                               failure_policy="disable"))
        self.applier = ConfigApplier(self.resolver, _Storage(sessions), lambda: dict(os.environ))
        self.applier.register("tag-scheduler", _ScheduleInstaller(schedule))

    def reconcile(self):
        return self.applier.reconcile("tag-scheduler").status == "APPLIED"

    def sync_rule(self, rule_id):
        return self.reconcile()

    def scan_enabled(self):
        with self.schedule._scheduler.configuration_guard():
            return (self.applier.state("tag-scheduler").available
                    and self.schedule.effective_enabled is True)

    def describe(self):
        state = asdict(self.applier.state("tag-scheduler"))
        try:
            snapshots = self.applier.snapshots("tag-scheduler")
        except Exception:
            state.update(status="FAILED", error_code="CONFIG_APPLY_FAILED")
            return dict(sections=[], apply=state)
        desired = self.applier._token(snapshots)
        if desired != state["desired_token"]:
            state.update(desired_token=desired, status="PENDING", error_code=None)
        return dict(sections=[dict(section=s.section, effective_value=s.value, effective_origin=s.origin,
                                   effective_token=s.effective_token, storage_token=s.storage_token,
                                   overridden=s.overridden) for s in snapshots],
                    apply=state)
