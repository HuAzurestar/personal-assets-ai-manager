from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from typing import Iterator

from backend.entity.base import utc_now


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

    @property
    def expired(self) -> bool:
        return self.created_time < utc_now() - timedelta(hours=24)


class IntakePreviewStore:
    """Bounded process-local command state; never part of ledger SQL."""

    def __init__(self, maximum: int = 128):
        self.maximum = maximum
        self._states: dict[str, IntakePreviewState] = {}
        self._lock = RLock()

    def put(self, state: IntakePreviewState) -> None:
        with self._lock:
            self._purge()
            if len(self._states) >= self.maximum:
                oldest = min(self._states.values(), key=lambda item: item.created_time)
                self._states.pop(oldest.token, None)
            self._states[state.token] = copy.deepcopy(state)

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

    def _purge(self) -> None:
        expired = [token for token, state in self._states.items() if state.expired]
        for token in expired:
            self._states.pop(token, None)

    @staticmethod
    def _next_update_time(previous: datetime) -> datetime:
        return max(utc_now(), previous + timedelta(milliseconds=1))


target_intake_preview_store = IntakePreviewStore()
