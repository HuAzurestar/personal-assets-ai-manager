from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from typing import Iterator

from backend.core.config import IMPORT_PREVIEW_TIMEOUT_MINUTES
from backend.entity.base import utc_now


IMPORT_PREVIEW_TIMEOUT = timedelta(minutes=IMPORT_PREVIEW_TIMEOUT_MINUTES)


@dataclass(slots=True)
class IntakePreviewState:
    token: str
    documents: list[dict[str, object]]
    plan: dict[str, object]
    accounts: dict[str, str] = field(default_factory=dict)
    decisions: dict[str, str] = field(default_factory=dict)
    result: dict[str, object] | None = None
    created_time: datetime = field(default_factory=utc_now)
    updated_time: datetime = field(default_factory=utc_now)
    timeout: timedelta = IMPORT_PREVIEW_TIMEOUT

    def __post_init__(self) -> None:
        if self.timeout <= timedelta(0):
            raise ValueError("preview timeout must be positive")

    @property
    def timed_out(self) -> bool:
        return self.updated_time < utc_now() - self.timeout


class IntakePreviewStore:
    """Bounded process-local command state; never part of ledger SQL."""

    def __init__(self, maximum: int = 128):
        self.maximum = maximum
        self._states: dict[str, IntakePreviewState] = {}
        self._lock = RLock()

    def put(self, state: IntakePreviewState) -> tuple[IntakePreviewState, ...]:
        with self._lock:
            removed = []
            if len(self._states) >= self.maximum:
                oldest = min(self._states.values(), key=lambda item: item.created_time)
                removed.append(self._states.pop(oldest.token))
            self._states[state.token] = copy.deepcopy(state)
            return tuple(copy.deepcopy(item) for item in removed)

    def get(self, token: str) -> IntakePreviewState | None:
        with self._lock:
            state = self._states.get(token)
            return copy.deepcopy(state) if state is not None else None

    def replace(
        self,
        state: IntakePreviewState,
        *,
        expected_updated_time: datetime,
    ) -> bool:
        with self._lock:
            current = self._states.get(state.token)
            if current is None:
                raise KeyError(state.token)
            if current.updated_time != expected_updated_time:
                return False
            state.updated_time = self._next_update_time(current.updated_time)
            self._states[state.token] = copy.deepcopy(state)
            return True

    def touch(self, state: IntakePreviewState) -> None:
        with self._lock:
            current = self._states.get(state.token)
            if current is not state:
                raise KeyError(state.token)
            state.updated_time = self._next_update_time(state.updated_time)

    @contextmanager
    def locked(self, token: str) -> Iterator[IntakePreviewState | None]:
        with self._lock:
            state = self._states.get(token)
            yield state

    def clear(self) -> None:
        with self._lock:
            self._states.clear()

    @staticmethod
    def _next_update_time(previous: datetime) -> datetime:
        return max(utc_now(), previous + timedelta(microseconds=1))


target_intake_preview_store = IntakePreviewStore()
