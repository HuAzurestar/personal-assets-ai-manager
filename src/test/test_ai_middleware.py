"""AI middleware contracts with synthetic data and zero provider network I/O."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.orm import sessionmaker

from backend import target_main
from backend.core.job_scheduler import JobScheduler
from backend.entity import AutoTagRule, MAX_COUNTER_VALUE, TagAssignmentRequest
from backend.error import LlmAdapterError, SettingError
from backend.mapper.auto_tag_rule_mapper import AutoTagRuleMapper
from backend.mapper.auto_tag_scan_mapper import AutoTagScanMapper
from backend.middleware import ExecutionContext, PreparedTask, PromptVersion, TaskDefinition, create_middleware
from backend.middleware.entity.ai_invocation import AiInvocation
from backend.middleware.entity.ai_prompt import AiPrompt
from backend.middleware.runtime import TaskExecutor
from backend.middleware.schema.ai import AiInvocationListRequest, AiPromptCreateRequest, AiPromptPublishRequest
from backend.middleware.usage import extract_usage
from backend.router.dependency import get_db, get_middleware
from backend.schema.disclosure_preview import DisclosurePreviewRequest
from backend.schema.llm_analysis import LlmResolvedSuggestion
from test_auto_tag_scan import scan_runtime as scan_runtime, _seed_rule, _seed_ledger
from test_privacy_contract import _payload


def response(value, usage=None, cost=None):
    return dict(choices=[dict(index=0, finish_reason="stop", message=dict(
        role="assistant", content=json.dumps(value, ensure_ascii=False),
    ))], usage=usage, _hidden_params={"response_cost": cost})


def middleware(sessions, completion=None, *, tasks=None):
    return create_middleware(sessions, SimpleNamespace(get_for_provider=lambda _: "SYNTHETIC-KEY"),
                             JobScheduler(), executor=TaskExecutor(completion), tasks=tasks)


def draft(app, sessions, instruction="仅根据用途建议候选分类。"):
    with sessions() as db:
        return app.prompt(db).create(AiPromptCreateRequest(
            prompt_key="auto_tag.classify", instruction=instruction, note="offline test",
        ))


def publish(app, sessions, row, production=1):
    with sessions() as db:
        return app.prompt(db).publish(row["id"], AiPromptPublishRequest(
            expected_updated_time=row["updated_time"], expected_production_version=production,
        ))


def test_generic_task_uses_runtime_and_audit_without_tag_ids(scan_runtime):
    sessions, _, _, _ = scan_runtime

    class Input(BaseModel):
        model_config = ConfigDict(extra="forbid", frozen=True)
        word: str

    class Output(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
        label: str

    task = TaskDefinition(
        key="fixture.classify", version=3, title="Fictional second task",
        input_type=Input, output_type=Output,
        default_prompt=PromptVersion(key="fixture.classify", version=1, instruction="Choose a label."),
        prepare=lambda value, prompt, _: PreparedTask(
            messages=[{"role": "system", "content": prompt.instruction},
                      {"role": "user", "content": json.dumps(value.model_dump())}],
            response_format={"type": "json_object"},
        ),
        parse=lambda content, _: Output.model_validate_json(content),
    )
    calls = []

    def completion(**request):
        calls.append(request)
        with sessions() as db:
            # Audit is committed and the write slot is free during network work.
            db.execute(text("BEGIN IMMEDIATE"))
            record = db.scalars(select(AiInvocation)).one()
            assert record.status == "STARTED"
            assert (record.task_key, record.task_version, record.prompt_version) == (task.key, 3, 1)
            assert "SYNTHETIC-KEY" not in record.request_json
            db.rollback()
        return response({"label": "fictional"}, {"prompt_tokens": 12, "completion_tokens": 3}, "0.0000012")

    app = middleware(sessions, completion, tasks=(task,))
    with sessions() as db:
        app.prompt(db).seed()
    result = asyncio.run(app.ai.execute(task.key, input=Input(word="synthetic"), model_id=9,
                                       context=ExecutionContext(run_id="test-generic")))
    assert result.label == "fictional" and len(calls) == 1
    summary = app.ai.invocations.usage()
    assert summary == dict(calls=1, succeeded=1, calls_with_usage=1, reported_total_tokens=15,
                           calls_with_cost=1, estimated_cost_usd="0.000001200000", cost_source="SDK_ESTIMATE")
    item = app.ai.invocations.list(AiInvocationListRequest())["items"][0]
    assert item["run_id"] == "test-generic" and item["cached_tokens"] == -1
    assert not {"request_json", "response_text"} & item.keys()
    with pytest.raises(ValueError, match="already registered"):
        app.ai.registry.register(task)


@pytest.mark.parametrize("outcome,status", [("success", "SUCCEEDED"), ("reject", "REJECTED"), ("fail", "ERROR")])
def test_runtime_records_usage_for_rejected_output_and_safe_errors(scan_runtime, outcome, status):
    sessions, _, _, _ = scan_runtime
    payload = _payload()

    def completion(**_):
        if outcome == "fail":
            raise RuntimeError("PRIVATE-PROVIDER-BODY SYNTHETIC-KEY")
        value = dict(item=payload.item, decision="insufficient", suggestions=[])
        if outcome == "reject":
            value["private_extra"] = "PRIVATE-PROVIDER-BODY"
        return response(value, {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25})

    app = middleware(sessions, completion)
    call = app.ai.execute("auto_tag.classify", input=payload, model_id=9)
    if outcome == "success":
        assert asyncio.run(call).kind == "NO_SUGGESTION"
    else:
        with pytest.raises(LlmAdapterError) as caught:
            asyncio.run(call)
        assert "PRIVATE-PROVIDER-BODY" not in str(caught.value)
    item = app.ai.invocations.list(AiInvocationListRequest())["items"][0]
    assert item["status"] == status
    assert item["total_tokens"] == (-1 if outcome == "fail" else 25)
    assert "PRIVATE-PROVIDER-BODY" not in json.dumps(item, default=str)


def test_missing_invocation_table_prevents_provider_call(scan_runtime, tmp_path):
    _, _, _, _ = scan_runtime
    engine = create_engine(f"sqlite:///{tmp_path / 'missing-audit.db'}")
    sessions = sessionmaker(bind=engine)
    calls = []
    try:
        from backend.middleware.service.invocation_service import InvocationService
        from backend.service.auto_tag_task import AUTO_TAG_TASK
        from test_llm_adapter import _profile
        with pytest.raises(LlmAdapterError) as caught:
            TaskExecutor(lambda **_: calls.append(1)).execute(
                AUTO_TAG_TASK, _payload(), _profile(), AUTO_TAG_TASK.default_prompt,
                api_key="fictional", invocations=InvocationService(sessions),
            )
        assert caught.value.code == "AUDIT_STORAGE_ERROR" and calls == []
    finally:
        engine.dispose()


def test_publish_atomically_cancels_pending_and_rejects_inflight_scan(scan_runtime):
    sessions, _, view_id, tags = scan_runtime
    rule_id = _seed_rule(sessions, view_id)
    ledger_id = _seed_ledger(sessions, tags["unclassified"])
    app = middleware(sessions)
    with sessions() as db:
        app.prompt(db).seed()
    with sessions() as db:
        page = AutoTagScanMapper(db).read_page(rule_id, limit=100)
    with sessions() as db:
        committed = AutoTagScanMapper(db).commit_item(
            page.token, ledger_id=ledger_id, kind="SUGGESTED",
            suggestions=(LlmResolvedSuggestion(tag_id=tags["food"], tag_name="Food", reason="餐饮语义。"),),
        )
        assert committed.status == "COMMITTED"
    row = draft(app, sessions)
    assert row["state"] == "DRAFT" and row["version"] == 2
    with sessions() as db:
        assert app.prompt(db).resolve("auto_tag.classify").version == 1
    active = publish(app, sessions, row)
    assert active["state"] == "PRODUCTION"
    with sessions() as db:
        rule = AutoTagRuleMapper(db).get(rule_id)
        assert (rule["rule_revision"], rule["scan_epoch"], rule["scan_after_ledger_id"]) == (2, 2, 0)
        assert db.scalars(select(TagAssignmentRequest.status)).one() == 4
    with sessions() as db:
        stale = AutoTagScanMapper(db).commit_item(page.token, ledger_id=ledger_id, kind="NO_SUGGESTION")
    assert stale.status == "STALE" and stale.reason == "RULE_TOKEN_CHANGED"
    restarted = middleware(sessions)
    with sessions() as db:
        restarted.prompt(db).seed()
    with sessions() as db:
        assert restarted.prompt(db).resolve("auto_tag.classify").version == 2
    # A successful response token can immediately drive an idempotent retry.
    assert publish(app, sessions, active, production=2)["updated_time"] == active["updated_time"]


def test_publication_conflicts_do_not_change_production(scan_runtime):
    sessions, _, _, _ = scan_runtime
    app = middleware(sessions)
    with sessions() as db:
        app.prompt(db).seed()
    first, second = draft(app, sessions), draft(app, sessions)
    publish(app, sessions, first)
    with pytest.raises(SettingError) as caught:
        publish(app, sessions, second, production=1)
    assert caught.value.code == "AI_PROMPT_PRODUCTION_CONFLICT"
    with pytest.raises(SettingError) as caught:
        publish(app, sessions, first, production=2)
    assert caught.value.code == "AI_PROMPT_VERSION_CONFLICT"
    with sessions() as db:
        assert app.prompt(db).resolve("auto_tag.classify").version == 2
        assert db.get(AiPrompt, second["id"]).state == "DRAFT"


def test_exhausted_rule_revision_rolls_back_publication(scan_runtime):
    sessions, _, view_id, _ = scan_runtime
    rule_id = _seed_rule(sessions, view_id)
    app = middleware(sessions)
    with sessions() as db:
        app.prompt(db).seed()
    row = draft(app, sessions)
    with sessions() as db:
        db.execute(update(AutoTagRule).where(AutoTagRule.id == rule_id).values(rule_revision=MAX_COUNTER_VALUE))
        db.commit()
    with pytest.raises(SettingError) as caught:
        publish(app, sessions, row)
    assert caught.value.code == "AI_PROMPT_PUBLICATION_CONFLICT"
    with sessions() as db:
        assert app.prompt(db).resolve("auto_tag.classify").version == 1
        assert db.get(AiPrompt, row["id"]).state == "DRAFT"


def test_busy_database_rejects_draft_without_partial_write(scan_runtime):
    sessions, _, _, _ = scan_runtime
    app = middleware(sessions)
    with sessions() as db:
        app.prompt(db).seed()
    with sessions() as writer, sessions() as db:
        db.execute(text("PRAGMA busy_timeout=0"))
        db.commit()
        writer.execute(text("BEGIN IMMEDIATE"))
        with pytest.raises(SettingError) as caught:
            app.prompt(db).create(AiPromptCreateRequest(
                prompt_key="auto_tag.classify", instruction="Fictional draft",
            ))
        assert caught.value.status_code == 409
        assert caught.value.code == "AI_PROMPT_WRITE_CONFLICT"
        writer.rollback()
        assert list(db.scalars(select(AiPrompt.version))) == [1]


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": True, "completion_tokens": -2, "total_tokens": 1.5}])
def test_unknown_usage_is_explicit(usage):
    value = extract_usage({"usage": usage, "_hidden_params": {"response_cost": "NaN"}})
    assert (value.input_tokens, value.output_tokens, value.total_tokens, value.cost_usd) == (-1, -1, -1, "")


def test_production_prompt_used_for_calls_and_fixture_preview(scan_runtime):
    sessions, _, _, _ = scan_runtime
    requests = []
    payload = _payload()
    app = middleware(sessions, lambda **request: requests.append(request) or response(
        dict(item=payload.item, decision="insufficient", suggestions=[]),
    ))
    with sessions() as db:
        app.prompt(db).seed()
    row = draft(app, sessions, instruction="FICTIONAL-PROMPT-v2")
    publish(app, sessions, row)
    asyncio.run(app.ai.execute("auto_tag.classify", input=payload, model_id=9))
    assert requests[0]["messages"][0]["content"].startswith("FICTIONAL-PROMPT-v2")
    assert "不泄露身份" in requests[0]["messages"][0]["content"]
    with sessions() as db:
        preview = app.management(db).preview(row["id"], DisclosurePreviewRequest())
        assert preview.model_called is False
        assert preview.messages[0].content.startswith("FICTIONAL-PROMPT-v2")
    assert len(requests) == 1


def test_ai_api_pagination_validation_preview_and_publication(scan_runtime):
    sessions, _, _, _ = scan_runtime
    app = middleware(sessions, lambda **_: pytest.fail("Management must not call the provider"))
    with sessions() as db:
        app.prompt(db).seed()

    def database():
        with sessions() as db:
            yield db

    target_main.app.dependency_overrides[get_db] = database
    target_main.app.dependency_overrides[get_middleware] = lambda: app
    try:
        client = TestClient(target_main.app)
        root = "/paam/system/v1/ai"
        created = client.post(root + "/prompt", json={
            "prompt_key": "auto_tag.classify", "instruction": "FICTIONAL-v2",
        })
        assert created.status_code == 200, created.text
        row = created.json()["body"]
        preview = client.post(root + f"/prompt/{row['id']}/preview", json={"sample": "MEAL_SMALL"})
        assert preview.status_code == 200 and preview.json()["body"]["model_called"] is False
        assert client.post(root + f"/prompt/{row['id']}/preview", json={"ledger_id": 1}).status_code == 422
        assert client.post(root + f"/prompt/{row['id']}/publish", json={
            "expected_updated_time": row["updated_time"], "expected_production_version": 1,
        }).status_code == 200
        for name in ("task", "prompt", "invocation"):
            result = client.get(root + f"/{name}/list?page_index=1&page_size=1")
            assert result.status_code == 200, result.text
            body = result.json()["body"]
            assert set(body) == {"items", "total", "page_index", "page_size"}
            assert len(body["items"]) <= 1
            assert client.get(root + f"/{name}/list?page_size=101").status_code == 422
            for suffix, code in (("query=[{\"key\":\"name\",\"word\":\"x\"}]", "LIST_QUERY_NOT_SUPPORTED"),
                                 ("filter={\"key\":\"id\",\"op\":\"=\",\"val\":1}", "LIST_FILTER_NOT_SUPPORTED"),
                                 ("keyword=x", "LIST_PARAMETER_NOT_SUPPORTED")):
                error = client.get(root + f"/{name}/list?{suffix}")
                assert error.status_code == 422 and error.json()["body"]["code"] == code
        assert client.get(root + "/usage").json()["body"]["calls"] == 0
    finally:
        target_main.app.dependency_overrides.clear()
