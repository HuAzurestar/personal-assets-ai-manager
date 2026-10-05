"""One process-local scheduler and deduplicating FIFO worker for PAAM."""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from threading import RLock
from typing import Literal
from contextlib import nullcontext
from middleware.schedule import JobDefinition, TriggerSpec, JobRun, RunContext, RunControl, validate_task_key
from middleware.llm.contract import canonical
from middleware.llm.client import drain_on_cancel

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend.core.cron_expression import (
    APPLICATION_TIMEZONE,
    cron_trigger,
    validate_cron_expression,
)
from backend.core.config import DATA_DIR
from backend.core.schedule_diagnostics import PHASES, ScheduleDiagnostics, new_run_id, safe_code

TASK_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]*(?::[a-zA-Z0-9._-]+)+$")
DEFAULT_PAGE_LIMIT = 100
DEFAULT_SOFT_BUDGET_SECONDS = 30.0

QueueState = Literal["IDLE", "QUEUED", "RUNNING", "PAUSED", "BLOCKED"]
LastResult = Literal["COMPLETED", "PARTIAL_FAILURE", "SUCCEEDED", "YIELDED", "FAILED", "CANCELLED"]
BLOCKING_CODES = frozenset({
    "CONFIG_ERROR", "AUTH_ERROR", "MODEL_DISABLED", "NO_ACTIVE_TARGETS",
    "VIEW_INACTIVE", "REGISTER_FAILED", "COUNTER_EXHAUSTED",
})


@dataclass(frozen=True, slots=True)
class JobProgress:
    phase: str = "SCAN"
    page_total: int = 0
    inspected_count: int = 0
    submitted_count: int = 0
    request_count: int = 0
    failed_count: int = 0
    no_call_count: int = 0
    insufficient_count: int = 0
    skipped_count: int = 0
    attempt: int = 0
    rule_revision: int = 0


@dataclass(frozen=True, slots=True)
class JobOutcome:
    result: LastResult
    error_code: str | None = None
    outcome_code: str = "RUN_COMPLETED"


@dataclass(frozen=True, slots=True)
class JobRunContext:
    task_key: str
    started_at: datetime
    deadline_monotonic: float
    page_limit: int
    _monotonic: Callable[[], float] = field(repr=False, compare=False)
    _active: Callable[[], bool] = field(
        default=lambda: True, repr=False, compare=False,
    )
    run_id: str = ""
    _progress: Callable[[dict], None] = field(default=lambda _: None, repr=False, compare=False)
    _event: Callable[[dict], None] = field(default=lambda _: None, repr=False, compare=False)
    control: RunControl | None = field(default=None, repr=False, compare=False)
    _report: Callable[[dict], None] = field(default=lambda _: None, repr=False, compare=False)

    def may_start_work(self) -> bool:
        """Return false once this run's soft start budget is exhausted."""

        return self._monotonic() < self.deadline_monotonic and self.is_active()

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_monotonic - self._monotonic()) if self._active() else 0.0

    def is_active(self) -> bool:
        return self.control.may_start_work() if self.control else self._active()

    def may_commit(self) -> bool:
        return self.control.may_commit() if self.control else self._active()

    def admit_request(self):
        if self.control:
            return self.control.admit_request()
        if not self._active():
            from middleware.llm import LlmError
            raise LlmError("CANCELLED")
        return True

    def commit_guard(self):
        return self.control.commit_guard() if self.control else nullcontext(self._active())

    async def run_sync(self, callback, *args):
        return await drain_on_cancel(asyncio.create_task(asyncio.to_thread(callback, *args)))

    def report(self, *, phase, completed, total=None, metrics=None):
        self._report(dict(phase=phase, completed=completed, total=total, metrics=metrics or {}))

    def progress(self, **values) -> None:
        self._progress(values)

    def emit(self, code: str, *, phase: str, **values) -> None:
        self._event({"code": code, "phase": phase, **values})


JobCallback = Callable[[JobRunContext], Awaitable[JobOutcome | None]]


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    task_key: str
    queue_state: QueueState
    queue_position: int | None
    enqueued_at: datetime | None
    started_at: datetime | None
    next_run_at: datetime | None
    last_result: LastResult | None
    last_error_code: str | None
    run_id: str | None = None
    last_run_id: str | None = None
    elapsed_ms: int | None = None
    wait_ms: int | None = None
    progress: JobProgress = field(default_factory=JobProgress)
    last_progress: JobProgress | None = None
    last_outcome_code: str | None = None
    last_failure: dict | None = None
    blocked_attempts: int = 0


@dataclass(frozen=True, slots=True)
class SchedulerSnapshot:
    scheduler_state: Literal["RUNNING", "STOPPED"]
    worker_state: Literal["HEALTHY", "STOPPED", "UNHEALTHY"]
    accepting: bool
    captured_at: datetime
    tasks: tuple[JobSnapshot, ...]
    diagnostics_health: str = "HEALTHY"
    diagnostics_persistent: bool = False
    worker_heartbeat_at: datetime | None = None


@dataclass(slots=True)
class _Registration:
    task_key: str
    trigger: BaseTrigger
    callback: JobCallback
    paused: bool = False
    definition: JobDefinition | None = None
    parameters_json: str = "{}"


@dataclass(slots=True)
class _RuntimeState:
    enqueued_at: datetime | None = None
    started_at: datetime | None = None
    last_result: LastResult | None = None
    last_error_code: str | None = None
    run_id: str | None = None
    last_run_id: str | None = None
    started_monotonic: float | None = None
    enqueued_monotonic: float | None = None
    progress: JobProgress = field(default_factory=JobProgress)
    last_progress: JobProgress | None = None
    last_outcome_code: str | None = None
    last_failure: dict | None = None
    blocked_code: str | None = None
    blocked_attempts: int = 0


class JobScheduler:
    """Shared timer registry feeding a single, in-memory FIFO worker."""

    def __init__(
        self,
        *,
        wall_clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        page_limit: int = DEFAULT_PAGE_LIMIT,
        soft_budget_seconds: float = DEFAULT_SOFT_BUDGET_SECONDS,
        diagnostics: ScheduleDiagnostics | None = None,
    ):
        if page_limit <= 0:
            raise ValueError("page_limit must be positive")
        if soft_budget_seconds <= 0:
            raise ValueError("soft_budget_seconds must be positive")
        self._wall_clock = wall_clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic
        self._page_limit = page_limit
        self._soft_budget_seconds = soft_budget_seconds
        self._scheduler = self._new_scheduler()
        self._registrations: dict[str, _Registration] = {}
        self._states: dict[str, _RuntimeState] = {}
        self._queue: deque[str] = deque()
        self._active_keys: set[str] = set()
        self._running_key: str | None = None
        self._condition = asyncio.Condition()
        self._state_lock = RLock()
        self._worker_task: asyncio.Task[None] | None = None
        self._accepting = False
        self.diagnostics = diagnostics or ScheduleDiagnostics()
        self.diagnostics.register_identity("system:worker")
        self._worker_failed = False
        self._heartbeat_at: datetime | None = None
        self._registration_failures: set[str] = set()
        self._controls: dict[str, RunControl] = {}
        self._definitions: dict[str, JobDefinition] = {}
        self._generic_progress: dict[str, dict] = {}
        self._run_definitions = {}

    def register(self, definition: JobDefinition, trigger: TriggerSpec, *, parameters=None, paused=False):
        from pydantic import TypeAdapter
        parameters_json = canonical(parameters or {})
        if definition.parameter_schema is not None:
            adapter = TypeAdapter(definition.parameter_schema)
            value = adapter.validate_python(parameters or {}, strict=True)
            parameters_json = canonical(adapter.dump_python(value, mode="json"))
        with self._state_lock:
            self._definitions[definition.key] = definition
            self.diagnostics.register_definition(definition)
            if trigger.kind == "interval":
                self.register_interval(definition.key, seconds=trigger.value, callback=definition.handler, paused=paused)
            else:
                self.register_cron(definition.key, expression=trigger.value, callback=definition.handler, paused=paused)
            self._registrations[definition.key].definition = definition
            self._registrations[definition.key].parameters_json = parameters_json

    @staticmethod
    def _new_scheduler() -> AsyncIOScheduler:
        return AsyncIOScheduler(
            timezone=APPLICATION_TIMEZONE,
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 30,
            },
        )

    @property
    def running(self) -> bool:
        return self._accepting

    def configuration_guard(self):
        return self._state_lock

    def register_interval(
        self,
        task_key: str,
        *,
        seconds: float,
        callback: JobCallback,
        paused: bool = False,
    ) -> None:
        if seconds <= 0:
            raise ValueError("interval seconds must be positive")
        trigger = IntervalTrigger(
            seconds=seconds,
            timezone=APPLICATION_TIMEZONE,
        )
        self._register(task_key, trigger, callback, paused=paused)

    def register_cron(
        self,
        task_key: str,
        *,
        expression: str,
        callback: JobCallback,
        paused: bool = False,
    ) -> None:
        normalized = validate_cron_expression(expression)
        trigger = cron_trigger(normalized)
        if trigger.get_next_fire_time(None, self._now()) is None:
            raise ValueError("cron expression has no next run")
        self._register(
            task_key,
            trigger,
            callback,
            paused=paused,
        )

    def _register(
        self,
        task_key: str,
        trigger: BaseTrigger,
        callback: JobCallback,
        *,
        paused: bool,
    ) -> None:
        self._validate_task_key(task_key)
        if not callable(callback):
            raise TypeError("callback must be callable")
        self.diagnostics.register_identity(task_key)
        with self._state_lock:
            registration = _Registration(
                task_key=task_key,
                trigger=trigger,
                callback=callback,
                paused=paused,
            )
            self._registrations[task_key] = registration
            self._states.setdefault(task_key, _RuntimeState())
            accepting = self._accepting
        if accepting:
            try:
                self._install(registration)
            except Exception:
                self.registration_failed(task_key)
                raise RuntimeError("Schedule registration failed") from None
        with self._state_lock:
            self._registration_failures.discard(task_key)
            if self._states[task_key].blocked_code == "REGISTER_FAILED":
                self._states[task_key].blocked_code = None

    def registration_failed(self, task_key: str) -> None:
        self.diagnostics.register_identity(task_key)
        """Retain a visible failed registration, without a runnable stale job."""
        self._validate_task_key(task_key)
        with self._state_lock:
            if task_key in self._registration_failures:
                return
            self._registrations.pop(task_key, None)
            self._discard_queued(task_key)
            state = self._states.setdefault(task_key, _RuntimeState())
            state.blocked_code = state.last_error_code = "REGISTER_FAILED"
            state.last_result = "FAILED"
            state.last_run_id = new_run_id()
            state.last_failure = self.diagnostics.record(
                run_id=state.last_run_id, task_key=task_key,
                phase="REGISTER", code="REGISTER_FAILED",
            )
            self._registration_failures.add(task_key)
        if self._scheduler.running:
            try:
                self._scheduler.remove_job(task_key)
            except JobLookupError:
                pass

    async def start(self) -> None:
        if self._accepting:
            return
        if self._worker_task is not None:
            raise RuntimeError("scheduler worker did not finish shutting down")
        if self._scheduler.running:
            raise RuntimeError("APScheduler is already running")
        # FastAPI TestClient and process reloads may create a fresh event loop.
        # Rebuild loop-bound primitives only after the previous worker stopped.
        self._condition = asyncio.Condition()
        self.diagnostics.recover_interrupted()
        self._worker_failed = False
        self._accepting = True
        self._worker_task = asyncio.create_task(
            self._worker(),
            name="paam-job-worker",
        )
        self._worker_task.add_done_callback(self._worker_finished)
        self._heartbeat_at = self._now()
        try:
            with self._state_lock:
                registrations = tuple(self._registrations.values())
            for registration in registrations:
                try:
                    self._install(registration)
                except Exception:
                    self.registration_failed(registration.task_key)
            self._scheduler.start()
        except Exception:
            self._accepting = False
            self._worker_task.cancel()
            await self._await_cancelled_worker()
            raise

    async def shutdown(self) -> None:
        with self._state_lock:
            self._accepting = False
            for control in self._controls.values():
                control.cancel()
        if self._scheduler.running:
            self._scheduler.remove_all_jobs()
            self._scheduler.shutdown(wait=False)
        async with self._condition:
            with self._state_lock:
                while self._queue:
                    task_key = self._queue.popleft()
                    self._active_keys.discard(task_key)
                    state = self._states.get(task_key)
                    if state is not None:
                        state.enqueued_at = None
                        state.last_result = "CANCELLED"
                        state.last_error_code = None
            self._condition.notify_all()
        if self._worker_task is not None:
            self._worker_task.cancel()
            await self._await_cancelled_worker()
        with self._state_lock:
            self._running_key = None
            self._active_keys.clear()
            self._registrations.clear()
            self._states.clear()
            self._registration_failures.clear()
            self._controls.clear()
            self._run_definitions.clear()
            self._definitions.clear()
            self._generic_progress.clear()
            self._worker_failed = False
            self._heartbeat_at = None
        self._scheduler = self._new_scheduler()

    async def _await_cancelled_worker(self) -> None:
        worker = self._worker_task
        if worker is None:
            return
        try:
            await worker
        except asyncio.CancelledError:
            pass
        except Exception:
            # The done callback records only a fixed diagnostic; never a traceback.
            pass
        finally:
            self._worker_task = None

    async def notify(self, task_key: str) -> bool:
        """Queue one internal timer tick; duplicate queued/running ticks coalesce."""

        async with self._condition:
            with self._state_lock:
                registration = self._registrations.get(task_key)
                if (
                    not self._accepting
                    or self._worker_task is None
                    or self._worker_task.done()
                    or registration is None
                    or registration.paused
                    or task_key in self._active_keys
                ):
                    return False
                self._active_keys.add(task_key)
                self._queue.append(task_key)
                state = self._states[task_key]
                state.enqueued_at = self._now()
                state.enqueued_monotonic = self._monotonic()
                state.started_at = None
                state.run_id = new_run_id()
            self._condition.notify()
            return True

    def pause(self, task_key: str) -> None:
        with self._state_lock:
            registration = self._required(task_key)
            registration.paused = True
            state = self._states.get(task_key)
            if state and state.run_id in self._controls:
                self._controls[state.run_id].pause()
            self._discard_queued(task_key)
        if self._scheduler.running:
            self._scheduler.pause_job(task_key)

    def resume(self, task_key: str) -> None:
        with self._state_lock:
            registration = self._required(task_key)
            registration.paused = False
        if self._scheduler.running:
            self._scheduler.resume_job(task_key)

    def remove(self, task_key: str) -> None:
        with self._state_lock:
            if task_key not in self._registration_failures:
                self._required(task_key)
            self._registrations.pop(task_key, None)
            state = self._states.get(task_key)
            if state and state.run_id in self._controls:
                self._controls[state.run_id].cancel()
            self._registration_failures.discard(task_key)
            self._discard_queued(task_key)
            if self._running_key != task_key:
                self._states.pop(task_key, None)
        if self._scheduler.running:
            try:
                self._scheduler.remove_job(task_key)
            except JobLookupError:
                pass

    def request_cancel(self, run_id: str) -> None:
        with self._state_lock:
            control = self._controls.get(run_id)
            if control is not None:
                control.cancel()
                return
            for key, state in self._states.items():
                if state.run_id == run_id and key in self._queue:
                    self._discard_queued(key)
                    state.last_result = "CANCELLED"
                    state.last_run_id, state.run_id = run_id, None
                    return
        raise KeyError(run_id)

    def snapshot(self) -> SchedulerSnapshot:
        with self._state_lock:
            positions = {
                task_key: index for index, task_key in enumerate(self._queue, start=1)
            }
            tasks = []
            for task_key in sorted(set(self._registrations) | self._registration_failures):
                registration = self._registrations.get(task_key)
                state = self._states[task_key]
                if self._running_key == task_key:
                    queue_state: QueueState = "RUNNING"
                elif task_key in positions:
                    queue_state = "QUEUED"
                elif registration is not None and registration.paused:
                    queue_state = "PAUSED"
                elif state.blocked_code is not None:
                    queue_state = "BLOCKED"
                else:
                    queue_state = "IDLE"
                tasks.append(
                    JobSnapshot(
                        task_key=task_key,
                        queue_state=queue_state,
                        queue_position=positions.get(task_key),
                        enqueued_at=state.enqueued_at,
                        started_at=state.started_at,
                        next_run_at=self._next_run_time(task_key),
                        last_result=state.last_result,
                        last_error_code=state.last_error_code,
                        run_id=state.run_id,
                        last_run_id=state.last_run_id,
                        elapsed_ms=self._elapsed(state.started_monotonic),
                        wait_ms=self._elapsed(state.enqueued_monotonic),
                        progress=state.progress,
                        last_progress=state.last_progress,
                        last_outcome_code=state.last_outcome_code,
                        last_failure=dict(state.last_failure) if state.last_failure else None,
                        blocked_attempts=state.blocked_attempts,
                    )
                )
            worker = self._worker_task
            return SchedulerSnapshot(
                scheduler_state="RUNNING" if self._accepting else "STOPPED",
                worker_state=(
                    "UNHEALTHY" if self._worker_failed
                    else "HEALTHY" if worker is not None and not worker.done() else "STOPPED"
                ),
                accepting=self._accepting,
                captured_at=self._now(),
                tasks=tuple(tasks),
                diagnostics_health=self.diagnostics.health,
                diagnostics_persistent=self.diagnostics.persistent,
                worker_heartbeat_at=self._heartbeat_at,
            )

    @staticmethod
    def preview_cron(
        expression: str,
        *,
        now: datetime | None = None,
    ) -> datetime:
        normalized = validate_cron_expression(expression)
        current = now or datetime.now(APPLICATION_TIMEZONE)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now requires a timezone")
        next_run = cron_trigger(normalized).get_next_fire_time(None, current)
        if next_run is None:
            raise ValueError("cron expression has no next run")
        return next_run

    async def _scheduled_tick(self, task_key: str) -> None:
        await self.notify(task_key)

    def _install(self, registration: _Registration) -> None:
        self._scheduler.add_job(
            self._scheduled_tick,
            trigger=registration.trigger,
            args=(registration.task_key,),
            id=registration.task_key,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        if registration.paused:
            self._scheduler.pause_job(registration.task_key)

    async def _worker(self) -> None:
        while True:
            async with self._condition:
                while True:
                    with self._state_lock:
                        if not self._accepting:
                            return
                        if self._queue:
                            task_key = self._queue.popleft()
                            registration = self._registrations.get(task_key)
                            if registration is None or registration.paused:
                                self._active_keys.discard(task_key)
                                continue
                            self._running_key = task_key
                            state = self._states[task_key]
                            state.enqueued_at = None
                            state.enqueued_monotonic = None
                            state.started_at = self._now()
                            state.started_monotonic = self._monotonic()
                            state.run_id = state.run_id or new_run_id()
                            control = RunControl()
                            self._controls[state.run_id] = control
                            self._run_definitions[state.run_id] = registration.definition
                            state.progress = JobProgress()
                            self._heartbeat_at = state.started_at
                            self.diagnostics.record(
                                run_id=state.run_id, task_key=task_key,
                                phase="SCAN", code="RUN_STARTED",
                            )
                            break
                    try:
                        # This is the shared worker's liveness wait, not a new timer.
                        await asyncio.wait_for(self._condition.wait(), timeout=5)
                    except asyncio.TimeoutError:
                        self._heartbeat_at = self._now()

            context = JobRunContext(
                task_key=task_key,
                started_at=state.started_at,
                deadline_monotonic=(self._monotonic() + self._soft_budget_seconds),
                page_limit=self._page_limit,
                _monotonic=self._monotonic,
                _active=lambda key=task_key, current=registration: (
                    self._registration_is_active(key, current)
                ),
                run_id=state.run_id,
                _progress=lambda values, key=task_key, run=state.run_id: self._progress(key, run, values),
                _event=lambda values, key=task_key, run=state.run_id: self._event(key, run, values),
                control=control,
                _report=lambda values, key=task_key, run=state.run_id: self._report_progress(key, run, values),
            )
            try:
                if registration.definition is not None:
                    context = RunContext(JobRun(state.run_id, task_key, state.started_at, registration.parameters_json),
                                         context.deadline_monotonic, control, self._monotonic,
                                         context._report, context._event)
                outcome = await registration.callback(context)
                if outcome is not None and (
                    not isinstance(outcome, JobOutcome)
                    or outcome.result not in {"COMPLETED", "PARTIAL_FAILURE", "SUCCEEDED", "YIELDED", "FAILED", "CANCELLED"}
                ):
                    raise ValueError("Scheduled callback returned an invalid outcome")
            except asyncio.CancelledError:
                with self._state_lock:
                    state.last_result = "CANCELLED"
                    state.last_error_code = None
                    state.last_outcome_code = "RUN_CANCELLED"
                raise
            except Exception:  # noqa: BLE001 - isolate arbitrary job callbacks
                with self._state_lock:
                    state.last_result = "FAILED"
                    state.last_error_code = "JOB_CALLBACK_FAILED"
                    state.last_outcome_code = "JOB_CALLBACK_FAILED"
            else:
                with self._state_lock:
                    state.last_result = (
                        (outcome.result if outcome is not None else "COMPLETED")
                        if self._accepting and control.may_commit() else "CANCELLED"
                    )
                    state.last_error_code = (
                        self._outcome_code(outcome.error_code, registration.definition) if self._accepting and outcome is not None
                        and outcome.error_code is not None
                        else None
                    )
                    state.last_outcome_code = self._outcome_code(outcome.outcome_code, registration.definition) if outcome else "RUN_COMPLETED"
            finally:
                with self._state_lock:
                    self._finish_run(task_key, state)
                    self._controls.pop(state.run_id, None)
                    self._run_definitions.pop(state.run_id, None)
                    state.started_at = None
                    state.started_monotonic = None
                    state.run_id = None
                    self._active_keys.discard(task_key)
                    self._running_key = None
                    if task_key not in self._registrations and task_key not in self._registration_failures:
                        self._states.pop(task_key, None)

    def _finish_run(self, task_key: str, state: _RuntimeState) -> None:
        if task_key in self._registration_failures:
            self.diagnostics.record(
                run_id=state.run_id or new_run_id(), task_key=task_key,
                phase="FINISH", code="RUN_CANCELLED",
            )
            state.last_result = "FAILED"
            state.last_error_code = state.last_outcome_code = state.blocked_code = "REGISTER_FAILED"
            state.last_run_id = state.last_failure["run_id"] if state.last_failure else None
            state.last_progress = state.progress
            return
        code = state.last_error_code or (
            "RUN_CANCELLED" if state.last_result == "CANCELLED" else state.last_outcome_code
        ) or "RUN_COMPLETED"
        repeated_block = state.blocked_code == code and code in BLOCKING_CODES
        if repeated_block:
            state.blocked_attempts += 1
            self.diagnostics.record(
                run_id=state.run_id or new_run_id(), task_key=task_key,
                phase="FINISH", code="BLOCKED_PROBE",
            )
        else:
            has_detail = state.last_failure and state.last_failure["run_id"] == state.run_id
            event = self.diagnostics.record(
                run_id=state.run_id or new_run_id(), task_key=task_key,
                phase="FINISH", code=(
                    "RUN_ENDED_WITH_ERRORS" if has_detail and state.last_error_code else code
                ),
                rule_revision=state.progress.rule_revision,
            )
            if event["severity"] == "ERROR" and not has_detail:
                state.last_failure = event
            state.blocked_attempts = int(code in BLOCKING_CODES)
        state.last_run_id = state.run_id
        state.last_progress = state.progress
        state.blocked_code = code if code in BLOCKING_CODES else None
        self._heartbeat_at = self._now()

    def _progress(self, task_key: str, run_id: str, values: dict) -> None:
        with self._state_lock:
            state = self._states.get(task_key)
            if state is None or state.run_id != run_id:
                return
            allowed = {}
            for key, value in values.items():
                if key == "phase" and value in PHASES:
                    allowed[key] = value
                elif key in JobProgress.__dataclass_fields__ and key != "phase" and type(value) is int and 0 <= value <= 2**63 - 1:
                    allowed[key] = value
            state.progress = replace(state.progress, **allowed)
            self._heartbeat_at = self._now()

    def _report_progress(self, task_key, run_id, values):
        import math
        with self._state_lock:
            state = self._states.get(task_key)
            definition = self._run_definitions.get(run_id)
            if state is None or state.run_id != run_id or definition is None:
                raise ValueError("Unknown registered run")
            if values["phase"] not in definition.phases:
                raise ValueError("Undeclared phase")
            for key in ("completed", "total"):
                value = values[key]
                if value is not None and (type(value) is not int or not 0 <= value < 2**63):
                    raise ValueError("Invalid progress count")
            if values["completed"] is None or (values["total"] is not None and values["completed"] > values["total"]):
                raise ValueError("Invalid progress total")
            metrics = values["metrics"]
            if not isinstance(metrics, dict) or set(metrics) - definition.metrics:
                raise ValueError("Undeclared metric")
            if any(type(v) not in {int, float} or not math.isfinite(v) or abs(v) >= 2**63 for v in metrics.values()):
                raise ValueError("Invalid metric value")
            self._generic_progress[task_key] = {**values, "metrics": dict(metrics)}
            self._heartbeat_at = self._now()

    def progress_snapshot(self, task_key):
        from copy import deepcopy
        with self._state_lock:
            if task_key not in self._states:
                raise KeyError(task_key)
            return deepcopy(self._generic_progress.get(task_key))

    @staticmethod
    def _outcome_code(code, definition):
        if definition is not None and code in definition.error_codes:
            return code
        return safe_code(code)

    def _event(self, task_key: str, run_id: str, values: dict) -> None:
        with self._state_lock:
            state = self._states.get(task_key)
            if state is None or state.run_id != run_id or task_key in self._registration_failures:
                return
            if state.blocked_code == values.get("code"):
                return
            allowed = {key: value for key, value in values.items() if key in {
                "phase", "code", "ledger_id", "rule_revision", "attempt", "detail_code",
            }}
            event = self.diagnostics.record(run_id=run_id, task_key=task_key, **allowed)
            if event["severity"] == "ERROR":
                state.last_failure = event

    def _worker_finished(self, worker: asyncio.Task) -> None:
        if not self._accepting:
            return
        with self._state_lock:
            self._accepting = False
            self._worker_failed = True
            # Consume exceptions without formatting them or exposing their chain.
            if not worker.cancelled():
                worker.exception()
            self.diagnostics.record(
                run_id=new_run_id(), task_key="system:worker",
                phase="WORKER", code="WORKER_UNHEALTHY",
            )

    def _elapsed(self, started: float | None) -> int | None:
        return max(0, int((self._monotonic() - started) * 1000)) if started is not None else None

    def _required(self, task_key: str) -> _Registration:
        registration = self._registrations.get(task_key)
        if registration is None:
            raise KeyError(task_key)
        return registration

    def _registration_is_active(
        self, task_key: str, registration: _Registration,
    ) -> bool:
        with self._state_lock:
            return (
                self._accepting
                and self._registrations.get(task_key) is registration
                and not registration.paused
            )

    def _discard_queued(self, task_key: str) -> None:
        if self._running_key == task_key:
            return
        if task_key in self._active_keys:
            self._queue = deque(queued for queued in self._queue if queued != task_key)
            self._active_keys.discard(task_key)
        state = self._states.get(task_key)
        if state is not None:
            state.enqueued_at = None
            state.enqueued_monotonic = None

    def _next_run_time(self, task_key: str) -> datetime | None:
        if not self._scheduler.running:
            return None
        job = self._scheduler.get_job(task_key)
        return job.next_run_time if job is not None else None

    def _now(self) -> datetime:
        value = self._wall_clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise RuntimeError("scheduler wall clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _validate_task_key(task_key: str) -> None:
        validate_task_key(task_key)


job_scheduler = JobScheduler(diagnostics=ScheduleDiagnostics(DATA_DIR / "schedule-logs"))
