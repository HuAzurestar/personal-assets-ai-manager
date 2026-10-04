import asyncio
import threading
import pytest
from pydantic import BaseModel, ConfigDict
from backend.core.job_scheduler import JobScheduler, JobOutcome
from middleware.schedule import JobDefinition, TriggerSpec, RunContext


class Parameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    batch_size: int


async def wait_idle(scheduler):
    for _ in range(300):
        if all(t.queue_state not in {"QUEUED", "RUNNING"} for t in scheduler.snapshot().tasks):
            return
        await asyncio.sleep(.005)
    raise AssertionError("Scheduler did not drain")


def definition(handler, *, phase="CLEAN", code="CLEAN_FAILED"):
    return JobDefinition("housekeeping:fixture", handler, frozenset({phase}), frozenset({"files"}),
                         frozenset({code}), Parameters)


def test_non_llm_job_parameters_custom_progress_codes_and_fifo_deduplication():
    async def scenario():
        scheduler, seen = JobScheduler(), []
        async def cleanup(context):
            assert isinstance(context, RunContext) and not hasattr(context, "page_limit")
            assert context.parameters == {"batch_size": 3}
            changed = context.parameters
            changed["batch_size"] = 999
            assert context.parameters["batch_size"] == 3
            seen.append(context.run_id)
            context.report(phase="CLEAN", completed=1, total=3, metrics={"files": 1})
            context.emit("CLEAN_FAILED", phase="CLEAN")
            return JobOutcome("FAILED", "CLEAN_FAILED")
        scheduler.register(definition(cleanup), TriggerSpec("interval", 60), parameters={"batch_size": 3})
        await scheduler.start()
        try:
            assert await scheduler.notify("housekeeping:fixture")
            assert not await scheduler.notify("housekeeping:fixture")
            queued = scheduler.snapshot().tasks[0].run_id
            await wait_idle(scheduler)
            task = scheduler.snapshot().tasks[0]
            assert task.last_error_code == "CLEAN_FAILED" and task.last_result == "FAILED"
            assert seen == [queued] and task.last_run_id == queued
            assert scheduler.progress_snapshot(task.task_key)["metrics"] == {"files": 1}
            assert any(e["code"] == "CLEAN_FAILED" for e in scheduler.diagnostics.events())
        finally:
            await scheduler.shutdown()
    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["pause", "cancel", "remove"])
def test_control_latches_current_run_resume_only_future(action):
    async def scenario():
        scheduler = JobScheduler()
        entered, release, seen = asyncio.Event(), asyncio.Event(), []
        async def job(context):
            seen.append(context)
            entered.set()
            await release.wait()
            assert not context.may_start_work()
            assert context.may_commit() == (action == "pause")
            return JobOutcome("SUCCEEDED")
        scheduler.register(definition(job), TriggerSpec("interval", 60), parameters={"batch_size": 1})
        await scheduler.start()
        try:
            await scheduler.notify("housekeeping:fixture")
            await entered.wait()
            if action == "pause":
                scheduler.pause("housekeeping:fixture")
                scheduler.resume("housekeeping:fixture")
            elif action == "cancel":
                scheduler.request_cancel(seen[0].run_id)
            else:
                scheduler.remove("housekeeping:fixture")
            assert not await scheduler.notify("housekeeping:fixture")
            release.set()
            await wait_idle(scheduler)
            if action != "remove":
                assert scheduler.snapshot().tasks[0].last_result == ("SUCCEEDED" if action == "pause" else "CANCELLED")
        finally:
            await scheduler.shutdown()
    asyncio.run(scenario())


def test_registration_update_keeps_original_parameters_and_declarations():
    async def scenario():
        scheduler = JobScheduler()
        entered, release, seen = asyncio.Event(), asyncio.Event(), []
        async def old(context):
            entered.set()
            await release.wait()
            assert context.parameters == {"batch_size": 1}
            context.report(phase="OLD", completed=1, metrics={"files": 1})
            seen.append("old")
            return JobOutcome("FAILED", "OLD_FAILED")
        async def new(context):
            assert context.parameters == {"batch_size": 2}
            seen.append("new")
            return JobOutcome("SUCCEEDED")
        scheduler.register(definition(old, phase="OLD", code="OLD_FAILED"), TriggerSpec("interval", 60), parameters={"batch_size": 1})
        await scheduler.start()
        try:
            await scheduler.notify("housekeeping:fixture")
            await entered.wait()
            scheduler.register(definition(new, phase="NEW", code="NEW_FAILED"), TriggerSpec("interval", 60), parameters={"batch_size": 2})
            assert not await scheduler.notify("housekeeping:fixture")
            release.set()
            await wait_idle(scheduler)
            assert scheduler.snapshot().tasks[0].last_error_code == "OLD_FAILED"
            assert await scheduler.notify("housekeeping:fixture")
            await wait_idle(scheduler)
            assert seen == ["old", "new"]
        finally:
            await scheduler.shutdown()
    asyncio.run(scenario())


def test_sync_worker_cancellation_keeps_same_key_occupied_until_exit():
    async def scenario():
        scheduler = JobScheduler()
        entered, release = threading.Event(), threading.Event()
        contexts = []
        def blocking():
            entered.set()
            assert release.wait(3)
        async def job(context):
            contexts.append(context)
            await context.run_sync(blocking)
            return JobOutcome("SUCCEEDED")
        scheduler.register(definition(job), TriggerSpec("interval", 60), parameters={"batch_size": 1})
        await scheduler.start()
        await scheduler.notify("housekeeping:fixture")
        assert await asyncio.to_thread(entered.wait, 2)
        scheduler.request_cancel(contexts[0].run_id)
        assert not await scheduler.notify("housekeeping:fixture")
        closing = asyncio.create_task(scheduler.shutdown())
        await asyncio.sleep(.02)
        assert not closing.done()
        release.set()
        await closing
    asyncio.run(scenario())


def test_parameters_and_diagnostics_reject_undeclared_fields():
    scheduler = JobScheduler()
    async def job(_):
        pass
    with pytest.raises(ValueError):
        scheduler.register(definition(job), TriggerSpec("interval", 60), parameters={"batch_size": "3"})
    with pytest.raises(ValueError):
        scheduler.diagnostics.record(run_id="a" * 32, task_key="unregistered:fixture", phase="SCAN", code="RUN_STARTED")
