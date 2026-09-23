"""One process-local scheduler and deduplicating FIFO worker for PAAM."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Literal

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.interval import IntervalTrigger

from backend.core.cron_expression import (
    APPLICATION_TIMEZONE,
    cron_trigger,
    validate_cron_expression,
)

logger = logging.getLogger(__name__)

TASK_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]*(?::[a-zA-Z0-9._-]+)+$")
DEFAULT_PAGE_LIMIT = 100
DEFAULT_SOFT_BUDGET_SECONDS = 30.0

QueueState = Literal["IDLE", "QUEUED", "RUNNING", "PAUSED"]
LastResult = Literal["COMPLETED", "PARTIAL_FAILURE", "FAILED", "CANCELLED"]


@dataclass(frozen=True, slots=True)
class JobOutcome:
    result: LastResult
    error_code: str | None = None


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

    def may_start_work(self) -> bool:
        """Return false once this run's soft start budget is exhausted."""

        return self._monotonic() < self.deadline_monotonic and self._active()


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


@dataclass(frozen=True, slots=True)
class SchedulerSnapshot:
    scheduler_state: Literal["RUNNING", "STOPPED"]
    worker_state: Literal["HEALTHY", "STOPPED"]
    accepting: bool
    captured_at: datetime
    tasks: tuple[JobSnapshot, ...]


@dataclass(slots=True)
class _Registration:
    task_key: str
    trigger: BaseTrigger
    callback: JobCallback
    paused: bool = False


@dataclass(slots=True)
class _RuntimeState:
    enqueued_at: datetime | None = None
    started_at: datetime | None = None
    last_result: LastResult | None = None
    last_error_code: str | None = None


class JobScheduler:
    """Shared timer registry feeding a single, in-memory FIFO worker."""

    def __init__(
        self,
        *,
        wall_clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        page_limit: int = DEFAULT_PAGE_LIMIT,
        soft_budget_seconds: float = DEFAULT_SOFT_BUDGET_SECONDS,
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
        self._register(
            task_key,
            cron_trigger(normalized),
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
            self._install(registration)

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
        self._accepting = True
        self._worker_task = asyncio.create_task(
            self._worker(),
            name="paam-job-worker",
        )
        try:
            with self._state_lock:
                registrations = tuple(self._registrations.values())
            for registration in registrations:
                self._install(registration)
            self._scheduler.start()
        except Exception:
            self._accepting = False
            self._worker_task.cancel()
            await self._await_cancelled_worker()
            raise

    async def shutdown(self) -> None:
        with self._state_lock:
            self._accepting = False
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
        self._scheduler = self._new_scheduler()

    async def _await_cancelled_worker(self) -> None:
        worker = self._worker_task
        if worker is None:
            return
        try:
            await worker
        except asyncio.CancelledError:
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
                    or registration is None
                    or registration.paused
                    or task_key in self._active_keys
                ):
                    return False
                self._active_keys.add(task_key)
                self._queue.append(task_key)
                state = self._states[task_key]
                state.enqueued_at = self._now()
                state.started_at = None
            self._condition.notify()
            return True

    def pause(self, task_key: str) -> None:
        with self._state_lock:
            registration = self._required(task_key)
            registration.paused = True
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
            self._required(task_key)
            self._registrations.pop(task_key)
            self._discard_queued(task_key)
            if self._running_key != task_key:
                self._states.pop(task_key, None)
        if self._scheduler.running:
            try:
                self._scheduler.remove_job(task_key)
            except JobLookupError:
                pass

    def snapshot(self) -> SchedulerSnapshot:
        with self._state_lock:
            positions = {
                task_key: index for index, task_key in enumerate(self._queue, start=1)
            }
            tasks = []
            for task_key in sorted(self._registrations):
                registration = self._registrations[task_key]
                state = self._states[task_key]
                if self._running_key == task_key:
                    queue_state: QueueState = "RUNNING"
                elif task_key in positions:
                    queue_state = "QUEUED"
                elif registration.paused:
                    queue_state = "PAUSED"
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
                    )
                )
            worker = self._worker_task
            return SchedulerSnapshot(
                scheduler_state="RUNNING" if self._accepting else "STOPPED",
                worker_state=(
                    "HEALTHY" if worker is not None and not worker.done() else "STOPPED"
                ),
                accepting=self._accepting,
                captured_at=self._now(),
                tasks=tuple(tasks),
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
                            state.started_at = self._now()
                            state.last_error_code = None
                            break
                    await self._condition.wait()

            context = JobRunContext(
                task_key=task_key,
                started_at=state.started_at,
                deadline_monotonic=(self._monotonic() + self._soft_budget_seconds),
                page_limit=self._page_limit,
                _monotonic=self._monotonic,
                _active=lambda key=task_key, current=registration: (
                    self._registration_is_active(key, current)
                ),
            )
            try:
                outcome = await registration.callback(context)
            except asyncio.CancelledError:
                with self._state_lock:
                    state.last_result = "CANCELLED"
                    state.last_error_code = None
                raise
            except Exception:  # noqa: BLE001 - isolate arbitrary job callbacks
                with self._state_lock:
                    state.last_result = "FAILED"
                    state.last_error_code = "JOB_CALLBACK_FAILED"
                logger.error("Scheduled callback failed for %s", task_key)
            else:
                with self._state_lock:
                    state.last_result = (
                        (outcome.result if outcome is not None else "COMPLETED")
                        if self._accepting else "CANCELLED"
                    )
                    state.last_error_code = (
                        outcome.error_code if self._accepting and outcome is not None
                        else None
                    )
            finally:
                with self._state_lock:
                    state.started_at = None
                    self._active_keys.discard(task_key)
                    self._running_key = None
                    if task_key not in self._registrations:
                        self._states.pop(task_key, None)

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
        if TASK_KEY_PATTERN.fullmatch(task_key) is None:
            raise ValueError("task_key must be a stable namespaced identifier")


job_scheduler = JobScheduler()
