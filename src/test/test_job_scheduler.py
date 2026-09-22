from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone

import pytest

from backend import target_main
from backend.core.job_scheduler import JobRunContext, JobScheduler
from backend.service.target_economic_service import TargetEconomicService
from backend.service.target_intake_service import TargetIntakeService


def _run(coroutine):
    return asyncio.run(coroutine)


def test_fifo_deduplicates_queued_and_running_ticks():
    async def scenario():
        scheduler = JobScheduler()
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        both_done = asyncio.Event()
        order = []

        async def first(context):
            order.append((context.task_key, "start"))
            first_started.set()
            await release_first.wait()
            order.append((context.task_key, "done"))

        async def second(context):
            order.append((context.task_key, "start"))
            order.append((context.task_key, "done"))
            both_done.set()

        scheduler.register_interval("tag-scan:7", seconds=3600, callback=first)
        scheduler.register_interval("tag-scan:8", seconds=3600, callback=second)
        await scheduler.start()
        try:
            assert await scheduler.notify("tag-scan:7") is True
            await asyncio.wait_for(first_started.wait(), timeout=1)
            assert await scheduler.notify("tag-scan:7") is False
            assert await scheduler.notify("tag-scan:8") is True
            assert await scheduler.notify("tag-scan:8") is False
            queued = scheduler.snapshot()
            states = {item.task_key: item for item in queued.tasks}
            assert states["tag-scan:7"].queue_state == "RUNNING"
            assert states["tag-scan:8"].queue_state == "QUEUED"
            assert states["tag-scan:8"].queue_position == 1
            release_first.set()
            await asyncio.wait_for(both_done.wait(), timeout=1)
            await asyncio.sleep(0)
            assert order == [
                ("tag-scan:7", "start"),
                ("tag-scan:7", "done"),
                ("tag-scan:8", "start"),
                ("tag-scan:8", "done"),
            ]
        finally:
            await scheduler.shutdown()

    _run(scenario())


def test_pause_drops_queued_tick_and_requires_later_cron_to_resume():
    async def scenario():
        scheduler = JobScheduler()
        blocker_started = asyncio.Event()
        release = asyncio.Event()
        paused_called = False

        async def blocker(_):
            blocker_started.set()
            await release.wait()

        async def paused(_):
            nonlocal paused_called
            paused_called = True

        scheduler.register_interval("tag-scan:1", seconds=3600, callback=blocker)
        scheduler.register_interval("tag-scan:2", seconds=3600, callback=paused)
        await scheduler.start()
        try:
            await scheduler.notify("tag-scan:1")
            await asyncio.wait_for(blocker_started.wait(), timeout=1)
            await scheduler.notify("tag-scan:2")
            scheduler.pause("tag-scan:2")
            assert scheduler.snapshot().tasks[1].queue_state == "PAUSED"
            release.set()
            await asyncio.sleep(0.02)
            assert paused_called is False
            scheduler.resume("tag-scan:2")
            assert paused_called is False
            assert await scheduler.notify("tag-scan:2") is True
            for _ in range(20):
                if paused_called:
                    break
                await asyncio.sleep(0.01)
            assert paused_called is True
        finally:
            await scheduler.shutdown()

    _run(scenario())


def test_pause_during_call_prevents_duplicate_and_does_not_auto_resume():
    async def scenario():
        scheduler = JobScheduler()
        started = asyncio.Event()
        release = asyncio.Event()
        call_count = 0

        async def callback(_):
            nonlocal call_count
            call_count += 1
            started.set()
            await release.wait()

        scheduler.register_interval("tag-scan:7", seconds=3600, callback=callback)
        await scheduler.start()
        try:
            await scheduler.notify("tag-scan:7")
            await asyncio.wait_for(started.wait(), timeout=1)
            scheduler.pause("tag-scan:7")
            assert await scheduler.notify("tag-scan:7") is False
            release.set()
            await asyncio.sleep(0.02)
            assert call_count == 1
            assert scheduler.snapshot().tasks[0].queue_state == "PAUSED"
            scheduler.resume("tag-scan:7")
            await asyncio.sleep(0.02)
            assert call_count == 1
        finally:
            await scheduler.shutdown()

    _run(scenario())


def test_callback_failure_releases_fifo_for_next_job():
    async def scenario():
        scheduler = JobScheduler()
        second_done = asyncio.Event()

        async def failure(_):
            raise RuntimeError("synthetic failure must stay inside worker")

        async def success(_):
            second_done.set()

        scheduler.register_interval("tag-scan:1", seconds=3600, callback=failure)
        scheduler.register_interval("tag-scan:2", seconds=3600, callback=success)
        await scheduler.start()
        try:
            await scheduler.notify("tag-scan:1")
            await scheduler.notify("tag-scan:2")
            await asyncio.wait_for(second_done.wait(), timeout=1)
            await asyncio.sleep(0)
            states = {item.task_key: item for item in scheduler.snapshot().tasks}
            assert states["tag-scan:1"].last_result == "FAILED"
            assert states["tag-scan:1"].last_error_code == "JOB_CALLBACK_FAILED"
            assert states["tag-scan:2"].last_result == "COMPLETED"
        finally:
            await scheduler.shutdown()

    _run(scenario())


def test_shutdown_cancels_running_and_queued_work():
    async def scenario():
        scheduler = JobScheduler()
        started = asyncio.Event()
        cancelled = asyncio.Event()
        queued_called = False

        async def running(_):
            try:
                started.set()
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def queued(_):
            nonlocal queued_called
            queued_called = True

        scheduler.register_interval("tag-scan:1", seconds=3600, callback=running)
        scheduler.register_interval("tag-scan:2", seconds=3600, callback=queued)
        await scheduler.start()
        await scheduler.notify("tag-scan:1")
        await asyncio.wait_for(started.wait(), timeout=1)
        await scheduler.notify("tag-scan:2")
        await scheduler.shutdown()
        assert cancelled.is_set()
        assert queued_called is False
        assert await scheduler.notify("tag-scan:1") is False
        snapshot = scheduler.snapshot()
        assert snapshot.scheduler_state == "STOPPED"
        assert snapshot.worker_state == "STOPPED"
        assert snapshot.tasks == ()

    _run(scenario())


def test_scheduler_can_reconstruct_registrations_after_restart():
    async def scenario():
        scheduler = JobScheduler()
        calls = []

        async def callback(context):
            calls.append(context.task_key)

        for _ in range(2):
            expected_callbacks = calls.count("system:recovery") + 1
            scheduler.register_interval(
                "system:recovery", seconds=3600, callback=callback
            )
            await scheduler.start()
            assert await scheduler.notify("system:recovery") is True
            for _ in range(20):
                if calls.count("system:recovery") == expected_callbacks:
                    break
                await asyncio.sleep(0.01)
            await scheduler.shutdown()
            calls.append("restart-marker")
        assert calls == [
            "system:recovery",
            "restart-marker",
            "system:recovery",
            "restart-marker",
        ]

    _run(scenario())


def test_context_exposes_page_limit_and_controllable_soft_budget():
    class Clock:
        monotonic = 100.0

        def read(self):
            return self.monotonic

    async def scenario():
        clock = Clock()
        scheduler = JobScheduler(monotonic=clock.read)
        observed = []
        completed = asyncio.Event()

        async def callback(context):
            observed.append((context.page_limit, context.may_start_work()))
            clock.monotonic += 30
            observed.append((context.page_limit, context.may_start_work()))
            completed.set()

        scheduler.register_interval("tag-scan:7", seconds=3600, callback=callback)
        await scheduler.start()
        try:
            await scheduler.notify("tag-scan:7")
            await asyncio.wait_for(completed.wait(), timeout=1)
            assert observed == [(100, True), (100, False)]
        finally:
            await scheduler.shutdown()

    _run(scenario())


def test_cron_preview_uses_hong_kong_timezone_and_rejects_invalid_input():
    now = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)
    next_run = JobScheduler.preview_cron("0 20 * * *", now=now)
    assert next_run.utcoffset() == timedelta(hours=8)
    assert (next_run.hour, next_run.minute) == (20, 0)
    with pytest.raises(ValueError):
        JobScheduler.preview_cron("* * * * 1", now=now)
    with pytest.raises(ValueError):
        JobScheduler.preview_cron("* * * * *", now=now.replace(tzinfo=None))


def test_target_lifespan_registers_import_sweep_in_shared_scheduler(monkeypatch):
    class FakeScheduler:
        def __init__(self):
            self.registration = None
            self.started = False
            self.stopped = False

        def register_interval(self, task_key, *, seconds, callback, paused=False):
            self.registration = (task_key, seconds, callback, paused)

        async def start(self):
            self.started = True

        async def shutdown(self):
            self.stopped = True

    fake = FakeScheduler()
    expired_calls = []
    monkeypatch.setattr(target_main, "job_scheduler", fake)
    monkeypatch.setattr(
        TargetIntakeService,
        "fail_orphaned_pending_files",
        lambda self: 0,
    )
    monkeypatch.setattr(
        TargetIntakeService,
        "fail_expired_pending_files",
        lambda self: expired_calls.append(True) or 0,
    )
    monkeypatch.setattr(TargetEconomicService, "backfill_defaults", lambda self: 0)

    async def scenario():
        async with target_main.lifespan(target_main.app):
            assert fake.started is True
            task_key, seconds, callback, paused = fake.registration
            assert task_key == "system:import-preview-timeout"
            assert seconds > 0
            assert paused is False
            await callback(_context(task_key))
            assert expired_calls == [True]
        assert fake.stopped is True

    _run(scenario())


def test_target_lifespan_has_no_feature_owned_timer_loop():
    source = inspect.getsource(target_main)
    assert "asyncio.sleep" not in source
    assert "asyncio.create_task" not in source
    assert "while True" not in source


def _context(task_key: str) -> JobRunContext:
    return JobRunContext(
        task_key=task_key,
        started_at=datetime.now(timezone.utc),
        deadline_monotonic=1.0,
        page_limit=100,
        _monotonic=lambda: 0.0,
    )
