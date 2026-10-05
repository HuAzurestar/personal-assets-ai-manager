"""PIRC-40 review regressions using fictional providers and temporary databases."""
import asyncio
import json
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.bootstrap import create_llm_client
from backend.entity import LlmPromptAudit
from backend.schema.setting import AutomationModelWrite
from backend.service.model_call_service import request_for
from backend.service.auto_tag_scan_service import AutoTagScanService
from backend.service.configured_llm_analyzer import ConfiguredLlmAnalyzer
from backend.service.llm_adapter import LiteLlmAdapter
from backend.service.llm_privacy_service import LlmPrivacyService
from backend.service.llm_call_recorder import SqlCallRecorder
from backend.service.runtime_config_service import RuntimeConfigService
from backend.service.auto_tag_schedule_service import AutoTagScheduleService
from backend.service.schedule_status_service import ScheduleStatusService
from backend.mapper.setting_mapper import SettingMapper
from backend.core.job_scheduler import JobScheduler
from backend.core.llm_audit_migration import migrate_llm_audit
from middleware.llm import CallContext
from middleware.llm.provider import SDK_LOCK
from test_auto_tag_scan import scan_runtime, _seed_rule, _context
from test_scan_diagnostics import source_ledger
from test_llm_prompt_audit import audit_database
from test_middleware_llm import Secret, Admission, REQUEST, CONTEXT, connection, response
from test_llm_audit_migration import legacy, ASSET


def test_cancel_before_admission_must_prevent_send(audit_database, monkeypatch):
    lock_held, release, begun = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def hold_lock():
        with SDK_LOCK:
            lock_held.set()
            release.wait(10)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert lock_held.wait(3)

    async def scenario():
        client = create_llm_client(audit_database, Secret(), completion=lambda **kw: calls.append(1) or response())
        original = client.recorder.begin

        def begin(*args):
            call_id = original(*args)
            begun.set()
            return call_id

        monkeypatch.setattr(client.recorder, "begin", begin)
        task = asyncio.create_task(client.generate(REQUEST, connection(), CONTEXT, admission=Admission()))
        try:
            assert await asyncio.to_thread(begun.wait, 3)
            task.cancel()
            await asyncio.sleep(0.05)
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            release.set()
            await client.close()

    try:
        asyncio.run(scenario())
    finally:
        release.set()
        holder.join(3)
    with audit_database() as db:
        rows = db.scalars(select(LlmPromptAudit)).all()
        print("cancel-before-admission:", {"provider_calls": len(calls), "audit_statuses": [r.status for r in rows]})
    assert calls == [], "Cancelled before acquiring SDK lock, but a generation request was still sent"
    assert len(rows) == 1 and rows[0].status == "ERROR" and rows[0].error_code == "CANCELLED"
    assert json.loads(rows[0].metadata_json)["dispatch_state"] == "NOT_SENT"


@pytest.mark.parametrize("invalid_envelope", ["choice-index", "refusal", "tool-calls", "role", "finish"])
def test_configured_analyzer_must_preserve_envelope_rejection(scan_runtime, invalid_envelope):
    sessions, _, view_id, tags = scan_runtime
    source_ledger(sessions, tags["unclassified"])
    rule_id = _seed_rule(sessions, view_id)

    def complete(**kw):
        item = json.loads(kw["messages"][-1]["content"])["item"]
        raw = response(json.dumps({"item": item, "decision": "suggestion", "suggestions": [
            {"tag": "t1", "reason": "依据支出用途建议分类。"}]}))
        choice = raw["choices"][0]
        if invalid_envelope == "choice-index":
            choice["index"] = 1
        elif invalid_envelope == "refusal":
            choice["message"]["refusal"] = "Fictional refusal"
        elif invalid_envelope == "tool-calls":
            choice["message"]["tool_calls"] = [{"id": "fictional-tool"}]
        elif invalid_envelope == "role":
            choice["message"]["role"] = "user"
        else:
            choice["finish_reason"] = "length"
        return raw

    async def scenario():
        analyzer = ConfiguredLlmAnalyzer(sessions, Secret(), LiteLlmAdapter(complete))
        try:
            return await AutoTagScanService(sessions, analyzer).run_protected(rule_id, _context(), LlmPrivacyService())
        finally:
            await analyzer.close()

    report = asyncio.run(scenario())
    print("invalid-envelope:", invalid_envelope, report)
    assert report.failed_count == 1 and report.request_count == 0, "Invalid provider envelope was accepted and suggestion committed"


@pytest.mark.parametrize("option", [{"presence_penalty": 0.2}, {"reasoning_effort": "high"},
                                  {"frequency_penalty": -2}, {"top_p": 0.5}, {"seed": 0},
                                  {"max_completion_tokens": 65536}, {"extra_body": {"enable_thinking": False}}])
def test_saved_supported_model_options_must_be_callable(option):
    profile = AutomationModelWrite.model_validate({
        "id": 1, "name": "Fictional model", "enabled": True,
        "litellm_params": {"model": "openai/fixture", "api_base": "https://fixture.invalid/v1", **option},
    })
    print("accepted-setting-option:", option)
    request = request_for([{"role": "user", "content": "Fictional input"}], profile)
    assert request.unpack()["generation_options"].items() >= option.items()


@pytest.mark.parametrize("option", [{"unsupported_option": 0}, {"presence_penalty": 3},
                                  {"presence_penalty": False}, {"reasoning_effort": "unknown"},
                                  {"max_tokens": 65537}, {"temperature": 3},
                                  {"extra_body": {"unknown_flag": False}}])
def test_unsupported_options_are_rejected_before_save(option):
    with pytest.raises(ValidationError):
        AutomationModelWrite.model_validate({
            "id": 1, "name": "Fictional model", "enabled": False,
            "litellm_params": {"model": "openai/fixture", "api_base": "https://fixture.invalid/v1", **option},
        })


def test_completed_legacy_audit_must_not_block_new_semantics():
    driver, _ = legacy()
    driver.execute("UPDATE llm_prompt_audit SET response_truncated = 0")
    driver.commit()
    migrate_llm_audit(driver, ASSET)
    engine = create_engine("sqlite://", creator=lambda: driver)
    sessions = sessionmaker(bind=engine)
    try:
        with sessions() as db:
            row = db.scalars(select(LlmPromptAudit)).one()
            print("migrated-known-response:", row.status, row.metadata_json)
        # A completed prior analysis does not authorize restoring it for changed
        # semantics; it also is not an uncertain active call that forbids new work.
        context = CallContext("tag-scan", "new-rule-semantics", associations=(("rule_id", 1), ("ledger_id", 1)))
        SqlCallRecorder(sessions).begin(REQUEST, connection(), context)
    finally:
        engine.dispose()


@pytest.mark.parametrize("saved,effective", [(False, True), (True, False)])
def test_deployment_override_must_drive_schedule_guard(scan_runtime, monkeypatch, saved, effective):
    sessions, _, view_id, _ = scan_runtime
    _seed_rule(sessions, view_id)
    with sessions() as db:
        mapper = SettingMapper(db)
        mapper.begin_write()
        setting = mapper.get()
        setting["value"]["automation"]["scan_enabled"] = saved
        mapper.save(setting["value"], setting["updated_time"])
        mapper.commit()
    monkeypatch.setenv("PAAM_SCAN_ENABLED", json.dumps(effective))
    scheduler = JobScheduler()
    schedule = AutoTagScheduleService(sessions, scheduler, Secret(), real_analysis_enabled=True)
    try:
        runtime = RuntimeConfigService(sessions, schedule)
        assert runtime.reconcile()
        status = ScheduleStatusService(scheduler, sessions, synthetic_acceptance_enabled=False, real_analysis_enabled=True, runtime_config=runtime).get()
        print("deployment-override:", {"saved": saved, "effective": schedule.effective_enabled, "guard": status.tag_scan_guard})
        assert status.tag_scan_guard == ("REAL_READY" if effective else "DISABLED")
    finally:
        asyncio.run(schedule.close())


@pytest.mark.parametrize("mode", ["enabled", "disabled", "invalid", "install-failed"])
def test_schedule_status_api_uses_installed_availability(scan_runtime, monkeypatch, mode):
    from backend.core import target_database
    from backend.router import system

    sessions, _, view_id, _ = scan_runtime
    _seed_rule(sessions, view_id)
    scheduler = JobScheduler()
    schedule = AutoTagScheduleService(sessions, scheduler, Secret(), real_analysis_enabled=True)
    runtime = RuntimeConfigService(sessions, schedule)
    monkeypatch.setattr(target_database, "SessionLocal", sessions)
    monkeypatch.setattr(system, "job_scheduler", scheduler)
    monkeypatch.setattr(system, "AUTOTAG_REAL_ANALYSIS", True)
    monkeypatch.setenv("PAAM_SCAN_ENABLED", "false" if mode == "disabled" else "true")
    app = FastAPI()
    app.include_router(system.router)
    app.state.runtime_config = runtime
    try:
        if mode == "install-failed":
            monkeypatch.setattr(schedule, "_register", lambda *_: False)
        assert runtime.reconcile() == (mode != "install-failed")
        if mode == "invalid":
            monkeypatch.setenv("PAAM_SCAN_ENABLED", '"invalid"')
            assert not runtime.reconcile()
        with TestClient(app) as client:
            result = client.get("/paam/system/v1/schedule/status")
        assert result.status_code == 200
        assert result.json()["body"]["tag_scan_guard"] == ("REAL_READY" if mode == "enabled" else "DISABLED")
        if mode in {"invalid", "install-failed"}:
            assert runtime.describe()["apply"]["available"] is False
    finally:
        asyncio.run(schedule.close())
