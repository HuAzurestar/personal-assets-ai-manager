"""Declarations and thread-safe per-run control; no second timer engine."""
from __future__ import annotations

import math
import re
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from datetime import datetime
from dataclasses import field
import asyncio
import json
from middleware.llm.client import drain_on_cancel
from middleware.llm.contract import LlmError

TASK_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9-]*(?::[a-zA-Z0-9._-]+)+$")


def validate_task_key(value):
    if not isinstance(value, str) or len(value) > 96 or not TASK_KEY_PATTERN.fullmatch(value):
        raise ValueError("task_key must be a stable namespaced identifier")
    return value


@dataclass(frozen=True)
class TriggerSpec:
    kind: str
    value: str | float

    def __post_init__(self):
        if self.kind not in {"cron", "interval"}:
            raise ValueError("Invalid trigger kind")
        if self.kind == "interval" and (type(self.value) not in {int, float} or not math.isfinite(self.value) or self.value <= 0):
            raise ValueError("Invalid interval")
        if self.kind == "cron" and not isinstance(self.value, str):
            raise ValueError("Invalid cron expression")


@dataclass(frozen=True)
class JobDefinition:
    key: str
    handler: object
    phases: frozenset[str]
    metrics: frozenset[str] = frozenset()
    error_codes: frozenset[str] = frozenset()
    parameter_schema: object = None

    def __post_init__(self):
        validate_task_key(self.key)
        if not callable(self.handler) or not self.phases:
            raise ValueError("Handler and phases are required")
        for values in (self.phases, self.metrics, self.error_codes):
            if not isinstance(values, frozenset) or len(values) > 32:
                raise ValueError("Declarations must be bounded immutable sets")
            if any(not isinstance(v, str) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]{0,47}", v) for v in values):
                raise ValueError("Invalid diagnostic declaration")


class RunControl:
    def __init__(self):
        self.lock = RLock()
        self._paused = False
        self._cancelled = False

    def pause(self):
        with self.lock:
            self._paused = True

    def cancel(self):
        with self.lock:
            self._paused = self._cancelled = True

    def may_start_work(self):
        with self.lock:
            return not self._paused and not self._cancelled

    def may_commit(self):
        with self.lock:
            return not self._cancelled

    def admit_request(self):
        with self.lock:
            if self._paused or self._cancelled:
                raise LlmError("CANCELLED")
            return True

    @contextmanager
    def commit_guard(self):
        # Cancel and commit are linearized at this short boundary.
        with self.lock:
            yield not self._cancelled


@dataclass(frozen=True)
class JobRun:
    run_id: str
    task_key: str
    started_at: datetime
    parameters_json: str = field(default="{}", repr=False)

    @property
    def parameters(self):
        return json.loads(self.parameters_json)


@dataclass(frozen=True)
class RunContext:
    """Public execution contract; business page limits remain job parameters."""
    run: JobRun
    deadline_monotonic: float
    control: RunControl = field(repr=False)
    _monotonic: object = field(repr=False)
    _report: object = field(repr=False)
    _event: object = field(repr=False)

    @property
    def run_id(self):
        return self.run.run_id

    @property
    def task_key(self):
        return self.run.task_key

    @property
    def parameters(self):
        return self.run.parameters

    def may_start_work(self):
        return self.control.may_start_work() and self._monotonic() < self.deadline_monotonic

    def may_commit(self):
        return self.control.may_commit()

    def remaining_seconds(self):
        return max(0.0, self.deadline_monotonic - self._monotonic()) if self.control.may_start_work() else 0.0

    def admit_request(self):
        return self.control.admit_request()

    def commit_guard(self):
        return self.control.commit_guard()

    def report(self, *, phase, completed, total=None, metrics=None):
        self._report(dict(phase=phase, completed=completed, total=total, metrics=metrics or {}))

    def emit(self, code, *, phase):
        self._event(dict(code=code, phase=phase))

    async def run_sync(self, callback, *args):
        return await drain_on_cancel(asyncio.create_task(asyncio.to_thread(callback, *args)))
