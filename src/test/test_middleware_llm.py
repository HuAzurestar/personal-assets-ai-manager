"""PIRC-40: durable attempts, real SDK HTTP counts and worker ownership."""
import asyncio
import json
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from sqlalchemy import select
from backend.bootstrap import create_llm_client
from backend.entity import LlmPromptAudit
from backend.mapper.llm_prompt_audit_mapper import LlmPromptAuditMapper
from backend.service.llm_call_recorder import SqlCallRecorder
from middleware.llm import CallContext, ConnectionSnapshot, LlmClient, LlmError, LlmRequest
from middleware.llm.provider import LiteLlmProvider, SDK_LOCK
from middleware.schedule import RunControl
from test_llm_prompt_audit import audit_database  # noqa: F401


class Secret:
    def get_for_provider(self, _):
        return "fictional-sdk-key"


class Admission:
    def __init__(self, control=None):
        self.control = control

    def admit(self, connection, operation):
        return self.control.admit_request() if self.control else True


def connection(base="https://fixture.invalid/v1"):
    return ConnectionSnapshot(1, 1, "fixture-effective", "openai/pirc40-fixture", base,
                              "model:1", allowed_target=base, timeout=0.15)


def response(content="fictional summary"):
    return {"id": "fixture-response", "model": "fixture-model", "choices": [{
        "index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content},
    }]}


REQUEST = LlmRequest.build([{"role": "user", "content": "Fictional monthly totals only."}])
CONTEXT = CallContext("monthly-summary")


@pytest.fixture
def local_provider(monkeypatch):
    import tiktoken
    import requests
    encoding = tiktoken.Encoding(name="pirc40-byte", pat_str=r"(?s).",
                                mergeable_ranks={bytes([i]): i for i in range(256)}, special_tokens={})
    monkeypatch.setattr(tiktoken, "get_encoding", lambda *_a, **_k: encoding)
    monkeypatch.setattr(tiktoken, "encoding_for_model", lambda *_a, **_k: encoding)
    monkeypatch.setenv("LITELLM_LOCAL_MODEL_COST_MAP", "true")
    monkeypatch.setenv("HTTPS_PROXY", "http://must-not-be-used.invalid:1")
    monkeypatch.delenv("PAAM_LLM_PROXY", raising=False)
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *_a, **_k: pytest.fail("Unexpected external request"))
    state = {"requests": [], "status": 200, "delay": 0, "location": None}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            state["requests"].append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            if state["delay"]:
                time.sleep(state["delay"])
            value = {**response(), "object": "chat.completion", "created": 1}
            if state["status"] != 200:
                value = {"error": {"message": "fictional provider error"}}
            raw = json.dumps(value).encode()
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            if state["location"]:
                self.send_header("Location", state["location"])
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["base"] = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


@pytest.mark.parametrize("status,delay,code", [(429, 0, "RATE_LIMITED"), (503, 0, "PROVIDER_FAILED"), (200, 0.4, "TIMEOUT")])
def test_real_sdk_has_one_generation_post_per_attempt(audit_database, local_provider, status, delay, code):
    local_provider.update(status=status, delay=delay)
    async def run():
        client = create_llm_client(audit_database, Secret())
        try:
            with pytest.raises(LlmError) as caught:
                await client.generate(REQUEST, connection(local_provider["base"]), CONTEXT, admission=Admission())
            assert caught.value.code == code
            assert caught.value.dispatch_state == "MAY_HAVE_EXECUTED"
        finally:
            await client.close()
    asyncio.run(run())
    assert len(local_provider["requests"]) == 1
    assert local_provider["requests"][0][0] == "/v1/chat/completions"
    with audit_database() as db:
        row = db.scalars(select(LlmPromptAudit)).one()
        assert row.source == "monthly-summary" and row.rule_id is None
        assert row.status == "ERROR" and row.error_code == code
        assert row.input_tokens is None and row.estimated_cost is None
        assert "fictional-sdk-key" not in row.request_json + row.metadata_json


def test_real_provider_pre_audit_failure_has_zero_http_posts(audit_database, local_provider, monkeypatch):
    async def run():
        client = create_llm_client(audit_database, Secret())
        monkeypatch.setattr(client.recorder, "begin", lambda *_: (_ for _ in ()).throw(RuntimeError("private database text")))
        try:
            with pytest.raises(LlmError, match="AUDIT_BEGIN_FAILED"):
                await client.generate(REQUEST, connection(local_provider["base"]), CONTEXT, admission=Admission())
        finally:
            await client.close()
    asyncio.run(run())
    assert local_provider["requests"] == []


def test_real_sdk_redirect_cannot_forward_credentials(audit_database, local_provider):
    local_provider.update(status=307, location="http://127.0.0.1:1/steal")
    async def run():
        client = create_llm_client(audit_database, Secret())
        try:
            with pytest.raises(LlmError):
                await client.generate(REQUEST, connection(local_provider["base"]), CONTEXT, admission=Admission())
        finally:
            await client.close()
    asyncio.run(run())
    assert len(local_provider["requests"]) == 1


def test_sdk_lock_wait_rechecks_pause_before_dispatch(audit_database):
    calls, entered, release = [], threading.Event(), threading.Event()
    def hold_lock():
        with SDK_LOCK:
            entered.set()
            assert release.wait(5)
    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert entered.wait(2)
    async def run():
        control = RunControl()
        client = create_llm_client(audit_database, Secret(), completion=lambda **kw: calls.append(1) or response())
        task = asyncio.create_task(client.generate(REQUEST, connection(), CONTEXT, admission=Admission(control)))
        try:
            for _ in range(100):
                with audit_database() as db:
                    ready = db.scalar(select(LlmPromptAudit.id))
                if ready:
                    break
                await asyncio.sleep(0.01)
            assert ready
            control.pause()
            release.set()
            with pytest.raises(LlmError, match="CANCELLED"):
                await task
        finally:
            release.set()
            await client.close()
    try:
        asyncio.run(run())
    finally:
        release.set()
        holder.join(2)
    assert calls == []
    with audit_database() as db:
        assert json.loads(db.scalar(select(LlmPromptAudit.metadata_json)))["dispatch_state"] == "NOT_SENT"


def test_cancelled_wait_retains_worker_lock_and_finishes_original_record(audit_database):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def complete(**kw):
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        return response()
    async def run():
        client = create_llm_client(audit_database, Secret(), completion=complete)
        first = asyncio.create_task(client.generate(REQUEST, connection(), CONTEXT, admission=Admission()))
        assert await asyncio.to_thread(entered.wait, 2)
        first.cancel()
        second = asyncio.create_task(client.generate(REQUEST, connection(), CONTEXT, admission=Admission()))
        await asyncio.sleep(0.05)
        assert not first.done() and len(calls) == 1
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert (await second).content == "fictional summary"
        await client.close()
    try:
        asyncio.run(run())
    finally:
        release.set()
    with audit_database() as db:
        rows = db.scalars(select(LlmPromptAudit).order_by(LlmPromptAudit.id)).all()
        assert len(rows) == 2 and all(row.status == "SUCCEEDED" for row in rows)


def test_post_audit_failure_keeps_original_attempt_and_blocks_next_scan(audit_database, monkeypatch):
    calls = []
    context = CallContext("tag-scan", "stable-op", associations=(("rule_id", 1), ("ledger_id", 2)))
    async def run():
        client = create_llm_client(audit_database, Secret(), completion=lambda **kw: calls.append(1) or response())
        original = client.recorder.finish
        monkeypatch.setattr(client.recorder, "finish", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("secret")))
        with pytest.raises(LlmError) as caught:
            await client.generate(REQUEST, connection(), context, admission=Admission())
        assert caught.value.code == "AUDIT_FINISH_FAILED" and not caught.value.retryable
        monkeypatch.setattr(client.recorder, "finish", original)
        with pytest.raises(LlmError, match="OPERATION_BLOCKED"):
            await client.generate(REQUEST, connection(), replace(context, operation_id="new-config-op"), admission=Admission())
        await client.close()
    asyncio.run(run())
    assert calls == [1]
    with audit_database() as db:
        row = db.scalars(select(LlmPromptAudit)).one()
        assert row.status == "STARTED"
        assert json.loads(row.metadata_json)["dispatch_state"] == "MAY_HAVE_EXECUTED"


def test_finish_is_idempotent_and_late_error_cannot_downgrade_success(audit_database):
    async def run():
        client = create_llm_client(audit_database, Secret(), completion=lambda **kw: response())
        result = await client.generate(REQUEST, connection(), CONTEXT, admission=Admission())
        recorder = SqlCallRecorder(audit_database)
        recorder.finish(result.call_id, response=result, error=None, dispatch_state="RESPONSE_RECEIVED")
        with pytest.raises(RuntimeError, match="Conflicting known"):
            recorder.finish(result.call_id, response=None, error=LlmError("TIMEOUT"), dispatch_state="MAY_HAVE_EXECUTED")
        await client.close()
    asyncio.run(run())
    with audit_database() as db:
        assert db.scalar(select(LlmPromptAudit.status)) == "SUCCEEDED"
