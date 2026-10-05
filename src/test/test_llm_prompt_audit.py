from __future__ import annotations

import json
import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.core.target_database import init_target_db
from backend.entity import LlmPromptAudit
from backend.error import LlmAdapterError
from backend.service.llm_adapter import LiteLlmAdapter
from backend.service.llm_prompt_audit_service import LlmPromptAuditService, PromptAuditContext
from backend.service.auto_tag_scan_service import AutoTagScanService
from backend.service.configured_llm_analyzer import ConfiguredLlmAnalyzer
from backend.service.llm_privacy_service import LlmPrivacyService
from test_auto_tag_scan import _context as scan_context, _seed_rule, scan_runtime
from test_scan_diagnostics import source_ledger
from test_llm_adapter import _profile, _provider
from test_privacy_contract import _payload


@pytest.fixture
def audit_database(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'prompt-audit.db'}",
        connect_args={"check_same_thread": False},
    )
    init_target_db(bind=engine)
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    try:
        yield sessions
    finally:
        engine.dispose()


def _context(attempt=1):
    return PromptAuditContext(
        run_id="a" * 32, rule_id=7, rule_revision=3,
        ledger_id=143, model_id=1, attempt=attempt,
    )


def test_outbound_prompt_is_committed_before_call_and_raw_insufficient_is_saved(audit_database):
    payload = _payload()
    content = json.dumps({
        "item": payload.item, "decision": "insufficient", "suggestions": [],
    }, ensure_ascii=False)
    observed = []

    def completion(**request):
        with audit_database() as db:
            row = db.scalars(select(LlmPromptAudit)).one()
            observed.append((row.status, row.request_json, row.response_text))
        assert request["api_key"] == "private-api-key"
        return _provider(content)

    result = LiteLlmAdapter(completion).analyze_protected(
        payload, _profile(), api_key="private-api-key",
        audit=LlmPromptAuditService(audit_database), audit_context=_context(),
    )
    assert result.kind == "NO_SUGGESTION"
    assert observed[0][0] == "STARTED" and observed[0][2] == ""
    request = json.loads(observed[0][1])
    assert set(request) == {"messages", "response_format"}
    assert request["messages"][1]["content"]
    assert "private-api-key" not in observed[0][1]
    with audit_database() as db:
        row = db.scalars(select(LlmPromptAudit)).one()
        assert (row.run_id, row.rule_id, row.rule_revision, row.ledger_id) == (
            "a" * 32, 7, 3, 143,
        )
        assert (row.model_id, row.attempt, row.status) == (1, 1, "INSUFFICIENT")
        assert row.response_text == content and row.error_code == ""


def test_rejected_model_output_is_saved_without_exposing_it_in_error(audit_database):
    payload = _payload()
    content = json.dumps({
        "item": payload.item, "decision": "insufficient", "suggestions": [],
        "private_provider_field": "private-provider-value",
    })
    with pytest.raises(LlmAdapterError) as caught:
        LiteLlmAdapter(lambda **_: _provider(content)).analyze_protected(
            payload, _profile(), api_key="private-api-key",
            audit=LlmPromptAuditService(audit_database), audit_context=_context(),
        )
    assert caught.value.code == "OUTPUT_SCHEMA_INVALID"
    assert "private-provider-value" not in str(caught.value)
    with audit_database() as db:
        row = db.scalars(select(LlmPromptAudit)).one()
        assert row.status == "REJECTED"
        assert row.response_text == content
        assert row.error_code == "OUTPUT_SCHEMA_INVALID"


def test_provider_exception_leaves_prompt_and_only_safe_error_code(audit_database):
    payload = _payload()

    def fail(**_):
        raise RuntimeError("private provider body and private-api-key")

    with pytest.raises(LlmAdapterError) as caught:
        LiteLlmAdapter(fail).analyze_protected(
            payload, _profile(), api_key="private-api-key",
            audit=LlmPromptAuditService(audit_database), audit_context=_context(),
        )
    with audit_database() as db:
        row = db.scalars(select(LlmPromptAudit)).one()
        assert row.status == "ERROR" and row.error_code == caught.value.code
        assert row.response_text == ""
        assert "private-api-key" not in row.request_json
        assert "private provider body" not in row.error_code


def test_missing_audit_table_prevents_unrecorded_provider_call(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'uninitialized.db'}")
    sessions = sessionmaker(bind=engine)
    calls = []
    try:
        with pytest.raises(LlmAdapterError) as caught:
            LiteLlmAdapter(lambda **_: calls.append(1)).analyze_protected(
                _payload(), _profile(), api_key="private-api-key",
                audit=LlmPromptAuditService(sessions), audit_context=_context(),
            )
        assert caught.value.code == "AUDIT_STORAGE_ERROR"
        assert calls == []
    finally:
        engine.dispose()


def test_real_scan_passes_ledger_revision_run_and_attempt_to_audit(scan_runtime):
    sessions, _, view_id, tags = scan_runtime
    ledger_id = source_ledger(sessions, tags["unclassified"])
    rule_id = _seed_rule(sessions, view_id)

    class Secret:
        def get_for_provider(self, model_id):
            assert model_id == 9
            return "private-api-key"

    def completion(**request):
        item = json.loads(request["messages"][1]["content"])["item"]
        return _provider(json.dumps({
            "item": item, "decision": "insufficient", "suggestions": [],
        }))

    analyzer = ConfiguredLlmAnalyzer(sessions, Secret(), LiteLlmAdapter(completion))
    context = replace(scan_context(), run_id="b" * 32)
    report = asyncio.run(AutoTagScanService(sessions, analyzer).run_protected(
        rule_id, context, LlmPrivacyService(),
    ))
    assert report.insufficient_count == 1
    with sessions() as db:
        row = db.scalars(select(LlmPromptAudit)).one()
        assert (row.run_id, row.rule_id, row.rule_revision) == ("b" * 32, rule_id, 1)
        assert (row.ledger_id, row.model_id, row.attempt) == (ledger_id, 9, 1)
        assert row.status == "SUCCEEDED"
