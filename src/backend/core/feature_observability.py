"""Bounded, local operational evidence. Never accepts business text or labels.

Not a financial audit, receipt store, replay mechanism, or database table.
Metrics contain only fixed operation names and aggregate counts/durations.
"""
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import inspect
import json
import math
from pathlib import Path
from threading import RLock
from time import monotonic, time
from uuid import uuid4

OPERATIONS = frozenset({"HTTP", "QUERY", "IMPORT_PARSE", "IMPORT_BATCH",
    "PREVIEW_SWEEP", "PUBLISH", "TAG_SCAN", "LLM_ADAPTER"})
METRICS = frozenset({"import_parse_duration_ms", "import_rows",
    "import_batch_rollback_count", "import_result_unknown_count",
    "preview_sweep_failed_count", "publish_duration_ms", "write_busy_count",
    "stale_preview_count", "query_duration_ms", "limit_count",
    "tag_invalidated", "manual_mapping_ambiguous", "scan_pending", "scan_failed",
    "adapter_duration"})
# Unknown codes collapse to OTHER. A regex permitting arbitrary uppercase
# strings would still allow a caller's name, account, or search text into logs.
CODES = frozenset({"OK", "OTHER", "INTERNAL_SERVER_ERROR", "VALIDATION_ERROR",
    "WRITE_BUSY", "QUERY_BUSY", "RESULT_UNKNOWN", "STALE_PREVIEW", "ENTITY_CHANGED",
    "RELATION_BROKEN", "ACCOUNT_RELATION_BROKEN", "TAG_RELATION_BROKEN",
    "PARSE_ERROR", "PARSE_BUSY", "PARSE_LIMIT", "IMPORT_MATCH_LIMIT", "DETAIL_LIMIT",
    "SUMMARY_LIMIT", "AGGREGATION_LIMIT", "TAG_IMPACT_LIMIT", "POSITION_LIMIT", "REQUEST_LIMIT",
    "CONFIG_ERROR", "AUTH_ERROR", "RATE_LIMIT", "REQUEST_TIMEOUT",
    "PROVIDER_UNAVAILABLE", "AUDIT_STORAGE_UNAVAILABLE", "SUGGESTION_STALE",
    "OUTPUT_SEMANTIC_INVALID", "INPUT_INVALID", "COMMIT_FAILED"})
_trace = ContextVar("paam_operational_trace", default=None)


def trace_id():
    return _trace.get()


@contextmanager
def trace_scope():
    current = _trace.get()
    token = _trace.set(current or uuid4().hex)
    try:
        yield _trace.get()
    finally:
        _trace.reset(token)


class FeatureObservability:
    MAX_BYTES = 10 * 1024 * 1024
    FILE_COUNT = 5  # current plus four rotated files, not five plus current
    MAX_AGE_SECONDS = 30 * 24 * 60 * 60

    def __init__(self, directory=None):
        self.directory = Path(directory) if directory is not None else None
        self._lock = RLock()
        self._events = deque(maxlen=100)
        self._metrics = {}
        self.storage_unavailable = False

    def configure(self, directory):
        with self._lock:
            self.directory = Path(directory)
            try:
                self._prepare()
                self.storage_unavailable = False
            except OSError:
                self.storage_unavailable = True

    def _paths(self):
        return [self.directory / ("operations.jsonl" if index == 0 else
                f"operations.jsonl.{index}") for index in range(self.FILE_COUNT)]

    def _prepare(self):
        # Only these five owned files are touched. No globs, recursive purge,
        # original bills, database, prompt audit, or scheduler log paths.
        if self.directory.is_symlink():
            raise OSError("owned log directory cannot be a symlink")
        self.directory.mkdir(parents=True, exist_ok=True)
        cutoff = time() - self.MAX_AGE_SECONDS
        for path in self._paths():
            if path.is_symlink():
                raise OSError("owned log file cannot be a symlink")
            if path.exists():
                # Appending must not extend the lifetime of the oldest event.
                oldest = path.stat().st_mtime
                if path.stat().st_size:
                    try:
                        with path.open("rb") as stream:
                            first = json.loads(stream.readline(2048))
                        oldest = datetime.fromisoformat(first["timestamp"]).timestamp()
                    except (ValueError, KeyError, TypeError):
                        oldest = 0  # invalid owned operational file, never evidence
                if oldest < cutoff:
                    path.unlink()

    @staticmethod
    def _number(value, maximum):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0
        if (isinstance(value, float) and not math.isfinite(value)) or value < 0:
            return 0
        return min(value, maximum)

    def metric(self, name, operation, value=1):
        if not isinstance(name, str) or not isinstance(operation, str) or name not in METRICS or operation not in OPERATIONS:
            return
        value = self._number(value, 9_000_000_000_000)
        with self._lock:
            key = (name, operation)
            count, total, maximum = self._metrics.get(key, (0, 0, 0))
            self._metrics[key] = (min(count + 1, 2**63 - 1),
                min(total + value, 9_000_000_000_000), max(maximum, value))

    def emit(self, operation, *, code="OK", row_count=0, duration_ms=0):
        if not isinstance(operation, str) or operation not in OPERATIONS:
            return
        code = code if isinstance(code, str) and code in CODES else "OTHER"
        event = dict(timestamp=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            level="INFO" if code == "OK" else "WARNING", event="OPERATION_FINISHED",
            trace_id=_trace.get() or uuid4().hex, operation=operation, code=code,
            row_count=int(self._number(row_count, 50000)),
            duration_ms=round(self._number(duration_ms, 86_400_000), 3))
        line = (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")
        with self._lock:
            self._events.append(event)
            if self.directory is None:
                return
            try:
                self._prepare()
                paths = self._paths()
                if paths[0].exists() and paths[0].stat().st_size + len(line) > self.MAX_BYTES:
                    if paths[-1].exists():
                        paths[-1].unlink()
                    for index in range(len(paths) - 2, -1, -1):
                        if paths[index].exists():
                            paths[index].replace(paths[index + 1])
                with paths[0].open("ab") as stream:
                    stream.write(line)
                self.storage_unavailable = False
            except OSError:
                # Operational evidence must never change a commit's outcome.
                self.storage_unavailable = True

    def snapshot(self):
        """Internal diagnostics only; no high-cardinality label or public API."""
        with self._lock:
            return dict(storage_unavailable=self.storage_unavailable,
                events=[dict(event) for event in self._events],
                metrics=[dict(name=name, operation=operation, count=value[0],
                    total=value[1], maximum=value[2])
                    for (name, operation), value in sorted(self._metrics.items())])


observability = FeatureObservability()


def failure_metrics(operation, code):
    if code == "WRITE_BUSY":
        observability.metric("write_busy_count", operation)
    if code in {"STALE_PREVIEW", "ENTITY_CHANGED", "SUGGESTION_STALE"}:
        observability.metric("stale_preview_count", operation)
    if code in {"PARSE_LIMIT", "IMPORT_MATCH_LIMIT", "DETAIL_LIMIT", "SUMMARY_LIMIT", "AGGREGATION_LIMIT",
                "TAG_IMPACT_LIMIT", "POSITION_LIMIT", "REQUEST_LIMIT"}:
        observability.metric("limit_count", operation)


@contextmanager
def observe(operation, duration_metric=None, *, log=True):
    started, code = monotonic(), "OK"
    with trace_scope():
        try:
            yield
        except BaseException as error:
            candidate = getattr(error, "code", "INTERNAL_SERVER_ERROR")
            code = candidate if isinstance(candidate, str) and candidate in CODES else "OTHER"
            failure_metrics(operation, code)
            raise
        finally:
            duration = (monotonic() - started) * 1000
            if duration_metric is not None:
                observability.metric(duration_metric, operation, duration)
            if log:
                observability.emit(operation, code=code, duration_ms=duration)


def observed(operation, duration_metric=None):
    def decorate(function):
        if inspect.iscoroutinefunction(function):
            @wraps(function)
            async def asynchronous(*args, **kwargs):
                with observe(operation, duration_metric):
                    return await function(*args, **kwargs)
            return asynchronous
        @wraps(function)
        def synchronous(*args, **kwargs):
            with observe(operation, duration_metric):
                return function(*args, **kwargs)
        return synchronous
    return decorate
