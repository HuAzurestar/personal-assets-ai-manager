"""Bounded, pinnable process-local import state; never a result-replay cache."""
from contextlib import contextmanager
import copy
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from uuid import uuid4

from backend.entity.base import utc_now
from backend.core.config import IMPORT_PREVIEW_TIMEOUT_MINUTES
from backend.core.import_identity import canonical_json
from backend.error import TargetIntakeError

MIB = 1024 * 1024


def fail(code, message, status=409):
    raise TargetIntakeError(status, message, code=code)


@dataclass(slots=True)
class ImportPreviewState:
    token: str
    files: list[dict]
    rows: dict[tuple[int, int], dict]
    candidates: dict[tuple[int, int], dict] = field(default_factory=dict)
    choices: dict[tuple[int, int], dict] = field(default_factory=dict)
    created_time: datetime = field(default_factory=utc_now)
    updated_time: datetime = field(default_factory=utc_now)
    timeout: timedelta = field(default_factory=lambda: timedelta(minutes=IMPORT_PREVIEW_TIMEOUT_MINUTES))
    status: str = "READY"

    @property
    def timed_out(self):
        return utc_now() - self.updated_time > self.timeout

    def byte_size(self):
        # Normalized UTF-8 retained-content budget, not a promise about RSS.
        value = dict(token=self.token, files=self.files,
            rows=[dict(identity=list(identity), value=row) for identity, row in sorted(self.rows.items())],
            candidates=[dict(identity=list(identity), value=row) for identity, row in sorted(self.candidates.items())],
            choices=[dict(identity=list(identity), value=row) for identity, row in sorted(self.choices.items())])
        return len(canonical_json(value).encode("utf-8"))

    def digest(self):
        # Same complete source/candidate/choice guard, without advisory status
        # or TTL. Keep one definition for Service checks and cache-local checks.
        value = dict(files=self.files,candidates=[dict(identity=list(key),
            premise=candidate['premise_hash'],choice=self.choices.get(key))
            for key,candidate in sorted(self.candidates.items())])
        return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


@dataclass
class PreviewLease:
    state: ImportPreviewState
    store: object
    claim_id: str

    def publish(self):
        self.store.publish(self.state, self.claim_id)


class ImportPreviewStore:
    def __init__(self, maximum=128, total_bytes=64 * MIB, single_bytes=24 * MIB):
        self.maximum, self.total_bytes, self.single_bytes = maximum, total_bytes, single_bytes
        self._states, self._sizes, self._claims = {}, {}, {}
        self._lock = RLock()

    def _size(self, state):
        size = state.byte_size()
        if size > self.single_bytes or size > self.total_bytes:
            fail("INPUT_LIMIT", "preview retained content exceeds memory budget", 413)
        return size

    def _evict(self, *, size, replacing=None):
        removed = []
        count = len(self._states) - int(replacing in self._states)
        used = sum(self._sizes.values()) - self._sizes.get(replacing, 0)
        candidates = sorted((state for token, state in self._states.items()
                             if token != replacing and token not in self._claims), key=lambda state: state.created_time)
        while count + 1 > self.maximum or used + size > self.total_bytes:
            if not candidates:
                fail("PREVIEW_BUSY", "all eviction candidates are executing", 503)
            state = candidates.pop(0)
            removed.append(state)
            count -= 1
            used -= self._sizes[state.token]
        # No mutation before capacity is proven: failed admission preserves state.
        for state in removed:
            self._states.pop(state.token)
            self._sizes.pop(state.token)
        return removed

    def put(self, state):
        size = self._size(state)
        with self._lock:
            if state.token in self._states:
                fail("PREVIEW_CHANGED", "preview token already exists")
            removed = self._evict(size=size)
            self._states[state.token] = copy.deepcopy(state)
            self._sizes[state.token] = size
            return removed

    def get(self, token):
        with self._lock:
            state = self._states.get(token)
            if state is None:
                fail("PREVIEW_UNAVAILABLE", "preview is not resident; re-upload and inspect persisted progress", 410)
            result = copy.deepcopy(state)
            result.status = "CONFIRMING" if token in self._claims else "READY"
            return result

    def ensure_current(self, token, expected_updated_time, expected_digest):
        """Check the full guard under the cache lock; expose no mutable state.

        List callers already hold their detached copy. Copying every original
        source envelope again merely to recheck time/digest costs O(full data)
        per page. This retains both guards, including cancellation/eviction,
        without copying private source content or holding a SQLite writer lock.
        """
        with self._lock:
            state = self._states.get(token)
            if state is None:
                fail('PREVIEW_UNAVAILABLE','preview is no longer resident',410)
            if state.updated_time != expected_updated_time or state.digest() != expected_digest:
                fail('PREVIEW_CHANGED','preview input changed')

    def replace(self, state, expected_updated_time):
        size = self._size(state)
        with self._lock:
            current = self._states.get(state.token)
            if current is None:
                fail("PREVIEW_UNAVAILABLE", "preview is no longer resident", 410)
            if state.token in self._claims:
                fail("PREVIEW_BUSY", "preview is executing")
            if current.updated_time != expected_updated_time:
                fail("PREVIEW_CHANGED", "preview input changed")
            removed = self._evict(size=size, replacing=state.token)
            state.updated_time = max(utc_now(), current.updated_time + timedelta(microseconds=1))
            self._states[state.token] = copy.deepcopy(state)
            self._sizes[state.token] = size
            return removed

    @contextmanager
    def claim(self, token, expected_updated_time):
        claim_id = uuid4().hex
        with self._lock:
            state = self._states.get(token)
            if state is None:
                fail("PREVIEW_UNAVAILABLE", "preview is not resident", 410)
            if token in self._claims:
                fail("PREVIEW_BUSY", "preview is already executing")
            if state.updated_time != expected_updated_time:
                fail("STALE_PREVIEW", "preview input changed")
            self._claims[token] = claim_id
            working = copy.deepcopy(state)
        try:
            # No global/cache lock while SQLite or another task does work.
            yield PreviewLease(working, self, claim_id)
        finally:
            with self._lock:
                if self._claims.get(token) == claim_id:
                    self._claims.pop(token)

    def publish(self, state, claim_id):
        size = self._size(state)
        with self._lock:
            if self._claims.get(state.token) != claim_id:
                fail("PREVIEW_CHANGED", "execution ownership expired")
            # A confirmation shrinks/updates its own bounded candidate rows.
            # Growth must not unexpectedly evict other pending-file contexts.
            if sum(self._sizes.values()) - self._sizes[state.token] + size > self.total_bytes:
                fail("INPUT_LIMIT", "updated preview exceeds cache capacity", 413)
            current = self._states[state.token]
            state.updated_time = max(utc_now(), current.updated_time + timedelta(microseconds=1))
            self._states[state.token] = copy.deepcopy(state)
            self._sizes[state.token] = size

    def remove(self, token):
        with self._lock:
            if token in self._claims:
                fail("PREVIEW_BUSY", "cannot cancel an executing preview")
            state = self._states.pop(token, None)
            self._sizes.pop(token, None)
            return state

    def active_file_ids(self):
        with self._lock:
            return {file["file_id"] for state in self._states.values() for file in state.files}

    def clear(self):
        with self._lock:
            if self._claims:
                fail("PREVIEW_BUSY", "cannot clear executing previews")
            self._states.clear()
            self._sizes.clear()


import_preview_store = ImportPreviewStore()
