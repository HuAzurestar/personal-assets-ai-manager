import json
import hashlib
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from backend.router import llm_management
from backend.router.error import register_error_handlers
from backend.service.llm_call_recorder import SqlCallRecorder
from middleware.llm.prompt import PromptTemplate, PromptVariableSpec, prompt_store
from middleware.llm import LlmResponse
from test_middleware_llm import REQUEST, CONTEXT, connection, response, Secret
from test_llm_prompt_audit import audit_database  # noqa: F401


def test_renderer_is_one_pass_no_execution_and_actual_content_fingerprint():
    source = '[{"role":"user","content":"{{value}} / {{other}}"}]'
    template = PromptTemplate("independent", "Independent", source)
    result = template.render({"value": "{{other}}", "other": "safe"}, (
        PromptVariableSpec("value"), PromptVariableSpec("other")))
    assert result[0]["content"] == "{{other}} / safe"
    assert template.fingerprint == hashlib.sha256(template.content_json.encode()).hexdigest()
    assert template.render({"value": "changed", "other": "safe"}, (
        PromptVariableSpec("value"), PromptVariableSpec("other")))[0]["content"] != result[0]["content"]


@pytest.mark.parametrize("placeholder", ["{{x.y}}", "{{x()}}", "{{ x }}", "{{x|filter}}", "{{x"])
def test_invalid_placeholder_syntax_rejected(placeholder):
    with pytest.raises(ValueError):
        PromptTemplate("test", "Test", json.dumps([{"role": "user", "content": placeholder}]))


def test_specs_are_caller_owned_and_enforce_type_length_required_unknown():
    template = PromptTemplate("test", "Test", '[{"role":"user","content":"{{value}}"}]')
    spec = (PromptVariableSpec("value", max_bytes=2),)
    for values in ({}, {"value": 3}, {"value": "long"}, {"value": "ok", "secret": "x"}):
        with pytest.raises(ValueError):
            template.render(values, spec)
    with pytest.raises(ValueError):
        template.render({"other": "x"}, (PromptVariableSpec("other"),))


def client_for(sessions):
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(llm_management.router)
    def db():
        with sessions() as value:
            yield value
    app.dependency_overrides[llm_management.get_db] = db
    return TestClient(app)


def test_prompt_management_is_readonly_and_preview_is_offline(audit_database):
    with client_for(audit_database) as client:
        items = client.get("/paam/system/v1/llm/prompt/list").json()["body"]
        assert {v["id"] for v in items} == {"tag-suggestion", "monthly-summary"}
        assert all(v["read_only"] and "messages" not in v for v in items)
        prefixes = []
        for item in items:
            base = f'/paam/system/v1/llm/prompt/{item["id"]}'
            preview = client.post(base + "/preview").json()["body"]
            assert preview["synthetic"] and preview["fingerprint"] == item["fingerprint"]
            prefixes.append(preview["messages"][0]["content"].split("\n")[0])
            assert client.get(base + "/reference").json()["body"] == []
            assert client.put(base, json={}).status_code == 405
            assert client.delete(base).status_code == 405
        assert prefixes[0] == prefixes[1]
        assert client.get("/paam/system/v1/llm/prompt/missing").status_code == 404


def test_audit_lists_statistics_never_select_body_detail_requires_authorization(audit_database, monkeypatch):
    recorder = SqlCallRecorder(audit_database)
    call_id = recorder.begin(REQUEST, connection(), CONTEXT)
    recorder.dispatch(call_id)
    recorder.finish(call_id, response=LlmResponse(call_id, "SENSITIVE_RESPONSE", "stop", "fixture"),
                    error=None, dispatch_state="RESPONSE_RECEIVED")
    statements = []
    bind = audit_database.kw["bind"]
    def capture(_, __, statement, *args):
        statements.append(statement)
    event.listen(bind, "before_cursor_execute", capture)
    try:
        with client_for(audit_database) as client:
            page = client.get("/paam/system/v1/llm/call/list").json()["body"]
            assert page["total"] == 1 and "response_text" not in page["items"][0]
            stats = client.get("/paam/system/v1/llm/call/statistics").json()["body"]
            assert stats["succeeded_count"] == 1 and stats["input_tokens"] is None
            assert all("request_json" not in sql and "response_text" not in sql for sql in statements)
            assert client.get(f"/paam/system/v1/llm/call/{call_id}").status_code == 403
            token = "fictional-detail-authorization-token-1234"
            monkeypatch.setenv("PAAM_LLM_AUDIT_DETAIL_TOKEN", token)
            assert client.get(f"/paam/system/v1/llm/call/{call_id}", headers={"x-paam-audit-token": "wrong"}).status_code == 403
            result = client.get(f"/paam/system/v1/llm/call/{call_id}", headers={"x-paam-audit-token": token})
            assert result.status_code == 200 and result.json()["body"]["response_text"] == "SENSITIVE_RESPONSE"
    finally:
        event.remove(bind, "before_cursor_execute", capture)
