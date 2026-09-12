from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from typing import Iterator


@dataclass(slots=True)
class IntakePreviewState:
    token: str
    documents: list[dict[str, object]]
    plan: dict[str, object]
    accounts: dict[str, str] = field(default_factory=dict)
    decisions: dict[str, str] = field(default_factory=dict)
    result: dict[str, object] | None = None
    created_time: datetime = field(default_factory=datetime.now)

    @property
    def expired(self) -> bool:
        return self.created_time < datetime.now() - timedelta(hours=24)


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

    def replace(self, state: IntakePreviewState) -> None:
        with self._lock:
            if state.token not in self._states:
                raise KeyError(state.token)
            self._states[state.token] = copy.deepcopy(state)

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


target_intake_preview_store = IntakePreviewStore()
