import asyncio
import json
import threading
from dataclasses import replace
import pytest
from pydantic import ValidationError
from sqlalchemy import select, update
from backend.bootstrap import create_llm_client
from backend.entity import AutoTagRule, LlmPromptAudit, TagAssignmentRequest
from backend.error import LlmAdapterError
from backend.mapper.auto_tag_scan_mapper import AutoTagScanMapper
from backend.schema.setting import LiteLLMParams, ModelCatalogRequest
from backend.schema.monthly_summary import DisclosedMonthlySummary, MonthlyCategory
from backend.service.monthly_summary_service import MonthlySummaryService
from backend.service.auto_tag_scan_service import AutoTagScanService
from backend.service.configured_llm_analyzer import ConfiguredLlmAnalyzer
from backend.service.llm_adapter import LiteLlmAdapter
from backend.service.llm_privacy_service import LlmPrivacyService
from middleware.llm import LlmError
from middleware.schedule import RunControl
from test_auto_tag_scan import scan_runtime, _seed_rule, _context  # noqa: F401
from test_scan_diagnostics import source_ledger
from test_middleware_llm import Secret, Admission, REQUEST, CONTEXT, connection, response
from test_llm_prompt_audit import audit_database  # noqa: F401


def test_monthly_use_case_has_independent_contract_and_nullable_tag_associations(scan_runtime):
    sessions, _, _, _ = scan_runtime
    seen = []
    def complete(**request):
        seen.append(json.loads(request["messages"][-1]["content"]))
        return response('{"summary":"Three fictional meals.","highlights":["Meals: 3"]}')
    async def run():
        service = MonthlySummaryService(sessions, Secret(), completion=complete)
        try:
            call_id, result = await service.summarize(DisclosedMonthlySummary(
                synthetic=True, month="2026-01", entry_count=3,
                categories=(MonthlyCategory(name="Meals", count=3),)), model_id=9)
            assert result.summary == "Three fictional meals."
            return call_id
        finally:
            await service.close()
    call_id = asyncio.run(run())
    assert set(seen[0]) == {"synthetic", "month", "entry_count", "categories"}
    with sessions() as db:
        row = db.get(LlmPromptAudit, call_id)
        assert row.source == row.prompt_id == "monthly-summary" and row.rule_id is None and row.ledger_id is None
        assert row.status == "SUCCEEDED" and row.total_tokens is None


def test_http_requires_explicit_saved_authorization_for_calls_and_catalog():
    with pytest.raises(ValidationError):
        LiteLLMParams(model="openai/local", api_base="http://127.0.0.1:8080/v1")
    params = LiteLLMParams(model="openai/local", api_base="http://127.0.0.1:8080/v1", allow_insecure_http=True)
    assert params.model_dump()["allow_insecure_http"] is True
    with pytest.raises(ValidationError):
        ModelCatalogRequest(provider="custom", api_base=params.api_base)
    assert ModelCatalogRequest(provider="custom", api_base=params.api_base, allow_insecure_http=True)
    for address in ("http://user:password@127.0.0.1/v1", "http://127.0.0.1/v1?api_key=x", "http://127.0.0.1/v1#fragment"):
        with pytest.raises(ValidationError):
            LiteLLMParams(model="openai/local", api_base=address, allow_insecure_http=True)


def test_credential_material_changed_after_resolution_blocks_actual_send(audit_database):
    class RotatingSecret:
        reads = 0
        def get_for_provider(self, _):
            self.reads += 1
            return "old-key" if self.reads == 1 else "new-key"
    calls = []
    async def run():
        client = create_llm_client(audit_database, RotatingSecret(), completion=lambda **kw: calls.append(1) or response())
        try:
            with pytest.raises(LlmError, match="AUTH_FAILED"):
                await client.generate(REQUEST, connection(), CONTEXT, admission=Admission())
        finally:
            await client.close()
    asyncio.run(run())
    assert calls == []
    with audit_database() as db:
        row = db.scalars(select(LlmPromptAudit)).one()
        assert row.status == "ERROR" and json.loads(row.metadata_json)["dispatch_state"] == "NOT_SENT"


def test_boolean_admission_denial_is_not_ignored(audit_database):
    class Deny:
        def admit(self, *_):
            return False
    calls = []
    async def run():
        client = create_llm_client(audit_database, Secret(), completion=lambda **kw: calls.append(1) or response())
        try:
            with pytest.raises(LlmError, match="CANCELLED"):
                await client.generate(REQUEST, connection(), CONTEXT, admission=Deny())
        finally:
            await client.close()
    asyncio.run(run())
    assert not calls


def test_saved_success_recovery_after_business_crash_uses_no_second_provider_call(scan_runtime):
    sessions, _, view_id, tags = scan_runtime
    ledger_id = source_ledger(sessions, tags["unclassified"])
    rule_id = _seed_rule(sessions, view_id)
    calls = []
    def complete(**kw):
        calls.append(1)
        item = json.loads(kw["messages"][-1]["content"])["item"]
        return response(json.dumps({"item": item, "decision": "insufficient", "suggestions": []}))
    class CrashCommit(AutoTagScanService):
        def _commit(self, *_a, **_kw):
            raise RuntimeError("simulated business commit failure")
    async def run():
        first = ConfiguredLlmAnalyzer(sessions, Secret(), LiteLlmAdapter(complete))
        report = await CrashCommit(sessions, first).run_protected(rule_id, _context(), LlmPrivacyService())
        assert report.stopped_reason == "COMMIT_FAILED"
        await first.close()
        # Independent lifespan, same durable DB; must consume the original response.
        second = ConfiguredLlmAnalyzer(sessions, Secret(), LiteLlmAdapter(complete))
        try:
            report = await AutoTagScanService(sessions, second).run_protected(rule_id, _context(), LlmPrivacyService())
            assert report.insufficient_count == 1
        finally:
            await second.close()
    asyncio.run(run())
    assert calls == [1]
    with sessions() as db:
        rule = db.get(AutoTagRule, rule_id)
        state = json.loads(rule.last_analysis_json)
        audit = db.scalars(select(LlmPromptAudit)).one()
        assert state == {"ledger_id": ledger_id, "call_id": audit.id, "validation": "PASSED", "commit": "COMMITTED"}
        assert audit.status == "SUCCEEDED" and rule.scan_after_ledger_id == ledger_id


def test_paused_admitted_item_commits(scan_runtime):
    sessions, _, view_id, tags = scan_runtime
    ledger_id = source_ledger(sessions, tags["unclassified"])
    rule_id = _seed_rule(sessions, view_id)
    control = RunControl()
    def complete(**kw):
        control.pause()
        item = json.loads(kw["messages"][-1]["content"])["item"]
        return response(json.dumps({"item": item, "decision": "insufficient", "suggestions": []}))
    async def run():
        analyzer = ConfiguredLlmAnalyzer(sessions, Secret(), LiteLlmAdapter(complete))
        try:
            result = await AutoTagScanService(sessions, analyzer).run_protected(rule_id, replace(_context(), control=control), LlmPrivacyService())
            assert result.insufficient_count == 1
        finally:
            await analyzer.close()
    asyncio.run(run())
    with sessions() as db:
        assert db.get(AutoTagRule, rule_id).scan_after_ledger_id == ledger_id


def test_cancelled_admitted_item_is_durably_blocked_after_new_lifespan(scan_runtime):
    sessions, _, view_id, tags = scan_runtime
    ledger_id = source_ledger(sessions, tags["unclassified"])
    rule_id = _seed_rule(sessions, view_id)
    control, calls = RunControl(), []
    def complete(**kw):
        calls.append(1)
        control.cancel()
        item = json.loads(kw["messages"][-1]["content"])["item"]
        return response(json.dumps({"item": item, "decision": "insufficient", "suggestions": []}))
    async def run():
        analyzer = ConfiguredLlmAnalyzer(sessions, Secret(), LiteLlmAdapter(complete))
        report = await AutoTagScanService(sessions, analyzer).run_protected(rule_id, replace(_context(), control=control), LlmPrivacyService())
        assert report.stopped_reason == "CANCELLED"
        await analyzer.close()
        replacement = ConfiguredLlmAnalyzer(sessions, Secret(), LiteLlmAdapter(complete))
        try:
            report = await AutoTagScanService(sessions, replacement).run_protected(rule_id, _context(), LlmPrivacyService())
            assert report.stopped_reason == "OPERATION_BLOCKED"
        finally:
            await replacement.close()
    asyncio.run(run())
    assert calls == [1]
    with sessions() as db:
        rule = db.get(AutoTagRule, rule_id)
        assert rule.scan_after_ledger_id == 0 and json.loads(rule.last_analysis_json)["commit"] == "CANCELLED"
        row = db.scalars(select(LlmPromptAudit)).one()
        assert row.status == "SUCCEEDED" and json.loads(row.metadata_json)["recovery_allowed"] is False


def test_midflight_rule_revision_change_rejects_business_submission(scan_runtime):
    sessions, _, view_id, tags = scan_runtime
    ledger_id = source_ledger(sessions, tags["unclassified"])
    rule_id = _seed_rule(sessions, view_id)
    def complete(**kw):
        with sessions() as db:
            db.execute(update(AutoTagRule).where(AutoTagRule.id == rule_id).values(rule_revision=2))
            db.commit()
        item = json.loads(kw["messages"][-1]["content"])["item"]
        return response(json.dumps({"item": item, "decision": "insufficient", "suggestions": []}))
    async def run():
        analyzer = ConfiguredLlmAnalyzer(sessions, Secret(), LiteLlmAdapter(complete))
        try:
            result = await AutoTagScanService(sessions, analyzer).run_protected(rule_id, _context(), LlmPrivacyService())
            assert result.stopped_reason == "RULE_TOKEN_CHANGED"
        finally:
            await analyzer.close()
    asyncio.run(run())
    with sessions() as db:
        rule = db.get(AutoTagRule, rule_id)
        assert rule.scan_after_ledger_id == 0 and json.loads(rule.last_analysis_json)["commit"] == "STALE"
        assert db.scalars(select(LlmPromptAudit)).one().status == "SUCCEEDED"
