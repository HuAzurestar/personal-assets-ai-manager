"""Operational evidence uses fictional inputs and isolated owned log paths."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from threading import Event

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from backend.core import feature_observability as feature
from backend.core.feature_observability import FeatureObservability, observe, observed, trace_scope
from backend.core.short_database_work import short_database_work
from backend.error import TargetEconomicError
from backend.router.error import DomainErrorRoute, register_error_handlers


def metric(snapshot, name):
    return [row for row in snapshot["metrics"] if row["name"] == name]


def test_closed_log_fields_and_low_cardinality_metrics(tmp_path):
    observer = FeatureObservability(tmp_path / "owned")
    with trace_scope() as trace:
        observer.emit("IMPORT_BATCH", code="PRIVATE_ACCOUNT_123", row_count=23, duration_ms=1.2345)
        observer.emit("PRIVATE_ACCOUNT_123")
        observer.metric("publish_duration_ms", "PUBLISH", 10)
        observer.metric("publish_duration_ms", "PUBLISH", 20)
        observer.metric("PRIVATE_ACCOUNT_123", "PUBLISH")
        observer.metric("publish_duration_ms", "PRIVATE_ACCOUNT_123")
    data = observer.snapshot()
    assert len(data["events"]) == 1 and len(data["metrics"]) == 1
    event = data["events"][0]
    assert set(event) == {"timestamp", "level", "event", "trace_id", "operation", "code", "row_count", "duration_ms"}
    assert (event["trace_id"], event["code"], event["row_count"], event["duration_ms"]) == (trace, "OTHER", 23, 1.234)
    assert metric(data, "publish_duration_ms")[0] == dict(name="publish_duration_ms", operation="PUBLISH", count=2, total=30, maximum=20)
    assert "PRIVATE_ACCOUNT_123" not in (tmp_path / "owned" / "operations.jsonl").read_text()


def test_numbers_and_untrusted_labels_never_escape_or_make_unbounded_state():
    observer = FeatureObservability()
    observer.emit("QUERY", code={"private": "name"}, row_count="12345", duration_ms=float("inf"))
    observer.metric("query_duration_ms", "QUERY", 2**10000)
    observer.emit(["private"])
    observer.metric(["private"], "QUERY")
    for index in range(200):
        observer.emit("HTTP", code=f"PERSON_{index}")
    data = observer.snapshot()
    assert len(data["events"]) == 100
    assert metric(data, "query_duration_ms")[0]["total"] == 9_000_000_000_000
    assert all(event["code"] == "OTHER" for event in data["events"])


def test_size_rotation_keeps_only_five_files_and_preserves_unrelated_evidence(tmp_path):
    observer = FeatureObservability(tmp_path)
    observer.MAX_BYTES = 700
    untouched = tmp_path / "original-statement.txt"
    untouched.write_text("fictional original evidence", encoding="utf-8")
    for _ in range(30):
        observer.emit("QUERY")
    files = [path for path in observer._paths() if path.exists()]
    assert len(files) == 5 and all(path.stat().st_size <= 700 for path in files)
    assert untouched.read_text() == "fictional original evidence"
    assert len(observer.snapshot()["events"]) == 30


def test_age_uses_oldest_event_not_recent_append_or_mtime(tmp_path):
    observer = FeatureObservability(tmp_path)
    path = tmp_path / "operations.jsonl"
    old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    path.write_text(json.dumps(dict(timestamp=old)) + "\n", encoding="utf-8")
    recent_path = tmp_path / "operations.jsonl.1"
    recent_path.write_text(json.dumps(dict(timestamp=datetime.now(timezone.utc).isoformat())) + "\n", encoding="utf-8")
    observer.emit("QUERY")
    assert old not in path.read_text() and recent_path.exists()


def test_disk_failure_keeps_safe_memory_and_does_not_change_return_or_commit(tmp_path, monkeypatch):
    observer = FeatureObservability(tmp_path)
    monkeypatch.setattr(feature, "observability", observer)
    original_open = Path.open

    def fail_owned(path, *args, **kwargs):
        if path.parent == tmp_path:
            raise PermissionError("fictional private path must not be logged")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_owned)
    committed = []

    @observed("PUBLISH", "publish_duration_ms")
    def publish():
        committed.append(True)
        return "COMMITTED"

    assert publish() == "COMMITTED" and committed == [True]
    assert observer.storage_unavailable and observer.snapshot()["events"][0]["code"] == "OK"
    assert "private" not in json.dumps(observer.snapshot())


def test_error_metrics_and_nested_trace_correlate_without_business_text(monkeypatch):
    observer = FeatureObservability()
    monkeypatch.setattr(feature, "observability", observer)
    with pytest.raises(TargetEconomicError):
        with observe("PUBLISH", "publish_duration_ms"):
            with observe("QUERY", "query_duration_ms"):
                raise TargetEconomicError(503, "fictional sensitive SQL and amount", code="WRITE_BUSY")
    data = observer.snapshot()
    assert len({row["trace_id"] for row in data["events"]}) == 1
    assert len(metric(data, "write_busy_count")) == 2
    assert "sensitive" not in json.dumps(data)


def test_http_trace_is_server_generated_and_errors_do_not_log_inputs(monkeypatch):
    import backend.router.error as errors
    observer = FeatureObservability()
    monkeypatch.setattr(feature, "observability", observer)
    monkeypatch.setattr(errors, "observability", observer)
    router = APIRouter(route_class=DomainErrorRoute)

    @router.get("/item/{item_id}")
    def item(item_id: int):
        with observe("QUERY", "query_duration_ms"):
            raise TargetEconomicError(409, "refresh current state", code="ENTITY_CHANGED")

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router)
    response = TestClient(app).get("/item/123?word=PRIVATE_SEARCH", headers={"X-PAAM-Trace-ID": "PRIVATE_TRACE"})
    trace = response.headers["X-PAAM-Trace-ID"]
    assert len(trace) == 32 and response.json()["body"]["details"]["trace_id"] == trace
    assert {row["trace_id"] for row in observer.snapshot()["events"]} == {trace}
    assert "PRIVATE" not in json.dumps(observer.snapshot())


def test_query_metrics_do_not_write_operational_files_inside_transactions(monkeypatch):
    from backend.core import target_database
    from backend.mapper.bounded_query_mapper import query_budget
    from sqlalchemy import text
    observer = feature.observability

    def forbidden(*args, **kwargs):
        raise AssertionError("query boundary must not perform log IO")

    monkeypatch.setattr(observer, "emit", forbidden)
    with target_database.SessionLocal() as db:
        with query_budget(db):
            assert db.scalar(text("SELECT 1")) == 1
        db.rollback()
    assert metric(observer.snapshot(), "query_duration_ms")[0]["count"] == 1


def test_validation_errors_also_have_object_details_and_correlated_trace():
    from test_router_error import _client
    response = _client().get("/validation?page=0")
    details = response.json()["body"]["details"]
    assert isinstance(details["field"], list) and details["field"]
    assert details["trace_id"] == response.headers["X-PAAM-Trace-ID"]


def test_repeated_shutdown_cancellation_drains_short_database_unit():
    started, release, finished = Event(), Event(), Event()

    def work():
        started.set()
        assert release.wait(3)
        finished.set()

    async def run():
        task = asyncio.create_task(short_database_work(work))
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(run())


def test_preview_sweep_whole_session_runs_off_event_loop(monkeypatch):
    from backend import target_main
    import threading
    threads = []
    main_thread = threading.get_ident()

    def sweep():
        threads.append(threading.get_ident())

    monkeypatch.setattr(target_main, "_sweep_import_previews", sweep)
    asyncio.run(target_main._sweep_timed_out_import_previews(None))
    assert len(threads) == 1 and threads[0] != main_thread
