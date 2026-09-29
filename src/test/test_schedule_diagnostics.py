from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.job_scheduler import JobOutcome, JobScheduler
from backend.core.schedule_diagnostics import MAX_ENTRY_BYTES, MAX_LOG_BYTES, ScheduleDiagnostics, new_run_id
from backend.core.target_database import init_target_db
from backend.router import system
from backend.router.error import register_error_handlers


def emit(store, code="OUTPUT_SEMANTIC_INVALID", **kwargs):
    return store.record(
        run_id=kwargs.pop("run_id", new_run_id()), task_key="tag-scan:3",
        phase=kwargs.pop("phase", "CALL"), code=code, **kwargs,
    )


def test_rotation_has_three_bounded_files_and_no_raw_values(tmp_path):
    store = ScheduleDiagnostics(tmp_path, max_bytes=2048)
    for _ in range(100):
        emit(store, detail_code="ITEM_MISMATCH", ledger_id=12, rule_revision=3, attempt=2)
    paths = sorted(tmp_path.iterdir())
    assert len(paths) == 3
    assert MAX_LOG_BYTES == 5 * 1024 * 1024
    assert all(path.stat().st_size <= 2048 for path in paths)
    assert all(len(line) <= MAX_ENTRY_BYTES for path in paths for line in path.read_bytes().splitlines())
    assert len(ScheduleDiagnostics(tmp_path).events()) < 100
    assert store.health == "HEALTHY"
    with pytest.raises(ValueError):
        ScheduleDiagnostics(tmp_path, max_bytes=MAX_LOG_BYTES + 1)


def test_default_file_limit_rotates_before_crossing_five_mib(tmp_path):
    # Exact file boundary, without tens of thousands of unnecessary test events.
    current = tmp_path / "schedule.jsonl"
    current.write_bytes(b" " * (MAX_LOG_BYTES - 32))
    emit(ScheduleDiagnostics(tmp_path))
    assert (tmp_path / "schedule.jsonl.1").stat().st_size == MAX_LOG_BYTES - 32
    assert current.stat().st_size < MAX_ENTRY_BYTES


def test_write_failure_retains_only_last_100_safe_events(tmp_path, monkeypatch, caplog):
    store = ScheduleDiagnostics(tmp_path)
    original = store._append

    def fail(_):
        raise OSError("SECRET_KEY raw bill 123456789 prompt private")

    monkeypatch.setattr(store, "_append", fail)
    for _ in range(150):
        emit(store)
    assert store.health == "DEGRADED"
    assert len(store.events()) == 100
    assert "SECRET_KEY" not in caplog.text
    assert "raw bill" not in json.dumps(store.events())
    monkeypatch.setattr(store, "_append", original)
    emit(store, "NO_DATA")
    assert store.health == "HEALTHY"


def test_codebook_rejects_untrusted_fields_and_revalidates_disk(tmp_path):
    store = ScheduleDiagnostics(tmp_path)
    event = store.record(
        task_key="account:123456789012345", run_id="private_nonce", phase="private prompt",
        code="RAW_PROVIDER_TEXT", detail_code="raw amount 2900", ledger_id=True,
    )
    assert event["code"] == "UNKNOWN_ERROR"
    assert event["task_key"] == "system:unknown"
    assert "ledger_id" not in event
    raw = json.dumps(event, ensure_ascii=False)
    assert all(word not in raw for word in ("private", "RAW_PROVIDER", "123456789", "2900"))
    event.update(safe_message="RAW_SECRET", prompt="private", output="private", provider_trace_id="raw")
    (tmp_path / "schedule.jsonl").write_text(json.dumps(event) + "\n", encoding="utf8")
    reread = ScheduleDiagnostics(tmp_path).events()
    assert len(reread) == 1
    assert "RAW_SECRET" not in json.dumps(reread)
    assert "prompt" not in reread[0] and "provider_trace_id" not in reread[0]


@pytest.mark.parametrize("bad", [b"{partial", b"x" * 2049, b'{"phase":[]}'])
def test_corrupted_history_is_visible_and_does_not_hide_valid_records(tmp_path, bad):
    store = ScheduleDiagnostics(tmp_path)
    emit(store)
    with (tmp_path / "schedule.jsonl").open("ab") as stream:
        stream.write(bad + b"\n")
    restored = ScheduleDiagnostics(tmp_path)
    assert len(restored.events()) == 1
    assert restored.health == "DEGRADED"

    (tmp_path / "schedule.jsonl").write_bytes(b"")
    assert restored.events() == []
    assert restored.health == "HEALTHY"


def test_restart_marks_unfinished_unknown_once_and_does_not_replay(tmp_path):
    first = ScheduleDiagnostics(tmp_path)
    unfinished, completed = new_run_id(), new_run_id()
    emit(first, "RUN_STARTED", phase="SCAN", run_id=unfinished)
    emit(first, "RUN_STARTED", phase="SCAN", run_id=completed)
    emit(first, "NO_DATA", phase="FINISH", run_id=completed)
    second = ScheduleDiagnostics(tmp_path)
    second.recover_interrupted()
    third = ScheduleDiagnostics(tmp_path)
    third.recover_interrupted()
    unknown = [item for item in third.events() if item["code"] == "PREVIOUS_RUN_UNKNOWN"]
    assert len(unknown) == 1 and unknown[0]["run_id"] == unfinished
    assert JobScheduler(diagnostics=third).snapshot().tasks == ()


async def finish_tick(scheduler, key):
    assert await scheduler.notify(key)
    for _ in range(100):
        await asyncio.sleep(.001)
        task = next(item for item in scheduler.snapshot().tasks if item.task_key == key)
        if task.queue_state not in {"QUEUED", "RUNNING"}:
            return task
    raise AssertionError("worker did not finish")


def test_natural_idle_heartbeats_keep_worker_alive_for_later_timer():
    # Exercise real wait_for timeouts on supported Python 3.10, not a fake clock.
    async def scenario():
        scheduler = JobScheduler()
        completed = asyncio.Event()

        async def maintenance(_):
            completed.set()
            return JobOutcome("COMPLETED")

        await scheduler.start()
        try:
            for _ in range(2):
                previous = scheduler.snapshot().worker_heartbeat_at

                async def next_heartbeat():
                    while True:
                        snapshot = scheduler.snapshot()
                        assert snapshot.worker_state == "HEALTHY" and snapshot.accepting
                        if snapshot.worker_heartbeat_at > previous:
                            return
                        await asyncio.sleep(.02)

                await asyncio.wait_for(next_heartbeat(), timeout=8)
            # A natural shared timer, registered after idling, must still execute.
            scheduler.register_interval(
                "system:import-preview-timeout", seconds=1, callback=maintenance,
            )
            await asyncio.wait_for(completed.wait(), timeout=3)
            assert scheduler.snapshot().tasks[0].last_result == "COMPLETED"
            assert scheduler.snapshot().worker_state == "HEALTHY"
            assert not any(event["code"] == "WORKER_UNHEALTHY" for event in scheduler.diagnostics.events())
        finally:
            await scheduler.shutdown()

    asyncio.run(scenario())


def test_live_progress_and_failure_survive_a_later_empty_tick(tmp_path):
    async def scenario():
        scheduler = JobScheduler(diagnostics=ScheduleDiagnostics(tmp_path))
        entered, release = asyncio.Event(), asyncio.Event()
        first = True

        async def work(context):
            nonlocal first
            if not first:
                return JobOutcome("COMPLETED", outcome_code="NO_DATA")
            first = False
            context.progress(phase="CALL", page_total=3, inspected_count=2, submitted_count=2, attempt=1)
            entered.set()
            await release.wait()
            context.emit("OUTPUT_SEMANTIC_INVALID", phase="CALL", detail_code="UNKNOWN_TAG", ledger_id=12)
            context.progress(phase="FINISH", failed_count=1, request_count=1)
            return JobOutcome("PARTIAL_FAILURE", "ITEM_FAILURE")

        scheduler.register_interval("tag-scan:3", seconds=3600, callback=work)
        await scheduler.start()
        try:
            await scheduler.notify("tag-scan:3")
            await asyncio.wait_for(entered.wait(), 1)
            current = scheduler.snapshot().tasks[0]
            assert current.queue_state == "RUNNING"
            assert current.progress.phase == "CALL" and current.progress.inspected_count == 2
            assert current.run_id and current.elapsed_ms is not None
            release.set()
            await asyncio.sleep(.01)
            failure = scheduler.snapshot().tasks[0].last_failure
            assert failure["detail_code"] == "UNKNOWN_TAG"
            empty = await finish_tick(scheduler, "tag-scan:3")
            assert empty.last_result == "COMPLETED" and empty.last_outcome_code == "NO_DATA"
            assert empty.last_failure == failure
            assert len([item for item in scheduler.diagnostics.events() if item["code"] == "OUTPUT_SEMANTIC_INVALID"]) == 1
        finally:
            await scheduler.shutdown()
    asyncio.run(scenario())


def test_repeated_auth_block_uses_cron_probe_but_deduplicates_diagnostics():
    async def scenario():
        scheduler = JobScheduler()
        calls = 0

        async def work(context):
            nonlocal calls
            calls += 1
            context.emit("AUTH_ERROR", phase="CALL")
            return JobOutcome("FAILED", "AUTH_ERROR")

        scheduler.register_interval("tag-scan:3", seconds=3600, callback=work)
        await scheduler.start()
        try:
            initial = await finish_tick(scheduler, "tag-scan:3")
            count = sum(item["severity"] == "ERROR" for item in scheduler.diagnostics.events())
            again = await finish_tick(scheduler, "tag-scan:3")
            assert calls == 2 and again.queue_state == "BLOCKED" and again.blocked_attempts == 2
            assert sum(item["severity"] == "ERROR" for item in scheduler.diagnostics.events()) == count
            assert scheduler.diagnostics.events()[0]["code"] == "BLOCKED_PROBE"
            assert again.last_failure == initial.last_failure
        finally:
            await scheduler.shutdown()
    asyncio.run(scenario())


def test_bad_registration_does_not_stop_import_maintenance(monkeypatch):
    async def scenario():
        scheduler = JobScheduler()
        original = scheduler._install

        def install(registration):
            if registration.task_key == "tag-scan:3":
                raise RuntimeError("sensitive registration exception")
            original(registration)

        async def good(_):
            return JobOutcome("COMPLETED")

        scheduler.register_interval("tag-scan:3", seconds=3600, callback=good)
        scheduler.register_interval("system:import-preview-timeout", seconds=3600, callback=good)
        monkeypatch.setattr(scheduler, "_install", install)
        await scheduler.start()
        try:
            failed = next(item for item in scheduler.snapshot().tasks if item.task_key == "tag-scan:3")
            assert failed.queue_state == "BLOCKED" and failed.last_error_code == "REGISTER_FAILED"
            assert failed.next_run_at is None
            assert (await finish_tick(scheduler, "system:import-preview-timeout")).last_result == "COMPLETED"
            assert not await scheduler.notify("tag-scan:3")
            assert "sensitive" not in json.dumps(scheduler.diagnostics.events())
        finally:
            await scheduler.shutdown()
    asyncio.run(scenario())


def test_worker_death_stops_accepting_and_is_not_healthy(monkeypatch, caplog):
    async def scenario():
        scheduler = JobScheduler()

        async def fail():
            raise RuntimeError("RAW_SECRET")

        monkeypatch.setattr(scheduler, "_worker", fail)
        await scheduler.start()
        await asyncio.sleep(.01)
        try:
            snapshot = scheduler.snapshot()
            assert snapshot.worker_state == "UNHEALTHY" and not snapshot.accepting
            assert not await scheduler.notify("system:maintenance")
            assert scheduler.diagnostics.events()[0]["code"] == "WORKER_UNHEALTHY"
            assert "RAW_SECRET" not in caplog.text
        finally:
            await scheduler.shutdown()
    asyncio.run(scenario())


def test_registration_failure_during_inflight_call_is_not_overwritten():
    async def scenario():
        scheduler = JobScheduler()
        entered, release = asyncio.Event(), asyncio.Event()

        async def work(context):
            entered.set()
            await release.wait()
            assert not context.is_active()
            context.emit("AUTH_ERROR", phase="CALL")  # Late old run must not hide the registration error.
            return JobOutcome("CANCELLED")

        scheduler.register_interval("tag-scan:3", seconds=3600, callback=work)
        await scheduler.start()
        try:
            await scheduler.notify("tag-scan:3")
            await asyncio.wait_for(entered.wait(), 1)
            scheduler.registration_failed("tag-scan:3")
            scheduler.registration_failed("tag-scan:3")
            release.set()
            await asyncio.sleep(.01)
            snapshot = scheduler.snapshot().tasks[0]
            assert snapshot.queue_state == "BLOCKED" and snapshot.last_error_code == "REGISTER_FAILED"
            assert snapshot.last_failure["code"] == "REGISTER_FAILED"
            assert len([event for event in scheduler.diagnostics.events() if event["code"] == "REGISTER_FAILED"]) == 1
        finally:
            await scheduler.shutdown()
    asyncio.run(scenario())


def test_bad_callback_result_is_isolated_instead_of_killing_worker():
    async def scenario():
        scheduler = JobScheduler()

        async def bad(_):
            return "raw provider details"

        async def good(_):
            return JobOutcome("COMPLETED")

        scheduler.register_interval("tag-scan:3", seconds=3600, callback=bad)
        scheduler.register_interval("system:maintenance", seconds=3600, callback=good)
        await scheduler.start()
        try:
            failed = await finish_tick(scheduler, "tag-scan:3")
            assert failed.last_error_code == "JOB_CALLBACK_FAILED"
            assert (await finish_tick(scheduler, "system:maintenance")).last_result == "COMPLETED"
            assert scheduler.snapshot().worker_state == "HEALTHY"
            assert "raw provider" not in json.dumps(scheduler.diagnostics.events())
        finally:
            await scheduler.shutdown()
    asyncio.run(scenario())


def test_diagnostic_routes_are_paginated_filtered_read_only(monkeypatch, tmp_path):
    init_target_db()
    store = ScheduleDiagnostics(tmp_path)
    scheduler = JobScheduler(diagnostics=store)
    for index in range(24):
        emit(store, "NO_DATA" if index % 2 else "AUTH_ERROR",
             now=datetime(2026, 9, 27, tzinfo=timezone.utc) + timedelta(seconds=index))
    scheduler.registration_failed("tag-scan:3")
    monkeypatch.setattr(system, "job_scheduler", scheduler)
    monkeypatch.setattr(system, "AUTOTAG_REAL_ANALYSIS", False)
    monkeypatch.setattr(system, "AUTOTAG_SYNTHETIC_ACCEPTANCE", False)
    app = FastAPI()
    app.include_router(system.router)
    register_error_handlers(app)
    with TestClient(app) as client:
        prefix = "/paam/system/v1/schedule"
        status = client.get(prefix + "/status").json()["body"]
        assert status["diagnostics_persistent"] and status["history_complete"] is False
        params = {"page_index": 2, "page_size": 5, "filter": json.dumps({"key": "severity", "op": "=", "val": "ERROR"})}
        page = client.get(prefix + "/event/list", params=params).json()["body"]
        assert page["total"] == 13 and len(page["items"]) == 5
        assert page["items"] == sorted(page["items"], key=lambda item: item["time"], reverse=True)
        task = client.get(prefix + "/task/list", params={"filter": json.dumps({"key": "queue_state", "op": "=", "val": "BLOCKED"})}).json()["body"]
        assert task["total"] == 1 and task["items"][0]["last_error_code"] == "REGISTER_FAILED"
        for params in ({"path": "../app.db"}, {"page_size": 101}, {"query": '[{"key":"prompt","word":"raw"}]'},
                       {"filter": '{"key":"ledger_id","op":"=","val":3}'},
                       {"filter": '{"key":"severity","op":"=","val":[]}'},
                       {"sorter": '[{"key":"time","direction":"asc"}]'}):
            assert client.get(prefix + "/event/list", params=params).status_code == 422
        assert client.post(prefix + "/event/list").status_code == 405
        assert client.post(prefix + "/run").status_code == 404
        assert len(store.events()) == 25  # No API read executes a scan or clears history.
