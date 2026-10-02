from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
from sqlalchemy import select

from backend.core.job_scheduler import JobRunContext, JobScheduler
from backend.entity import AutoTagRule, ReviewAllocation, TransactionFact
from backend.error import LlmAdapterError, ProtectedSecretStoreError
from backend.service.auto_tag_scan_service import AutoTagScanService, ScanRunReport
from backend.service.auto_tag_schedule_service import AutoTagScheduleService
from backend.service.configured_llm_analyzer import ConfiguredLlmAnalyzer
from backend.service.llm_adapter import _provider_exception, _retry_after_seconds, build_litellm_request
from backend.service.llm_privacy_service import LlmPrivacyService
from test_auto_tag_rule_api import _client, _rule_payload, _update_payload, auto_rule_runtime as auto_rule_runtime
from test_auto_tag_scan import (
    NOW,
    SequenceAnalyzer,
    _context,
    _fixture,
    _seed_ledger,
    _seed_rule,
    _suggest,
    scan_runtime as scan_runtime,
)
from test_llm_adapter import _payload, _profile


def source_ledger(sessions, tag_id):
    ledger_id = _seed_ledger(sessions, tag_id)
    with sessions() as db:
        # _seed_ledger already creates valid immutable source/default relations.
        # Customize fictional source text only; do not insert another Fact or
        # assume public Fact and Ledger IDs have the same meaning.
        fact_id = db.scalar(select(ReviewAllocation.transaction_fact_id).where(
            ReviewAllocation.ledger_entry_id == ledger_id,
        ))
        fact = db.get(TransactionFact, fact_id)
        assert fact is not None
        fact.counterparty_name = "餐厅"
        fact.summary = "餐饮消费"
        db.commit()
    return ledger_id


@pytest.mark.parametrize("raw, expected", [("4", 4), ("0.5", .5), ("-1", 0), ("inf", None),
                                          ("nan", None), ("private-raw-error", None), ("9" * 129, None)])
def test_retry_after_header_reduces_to_safe_number(raw, expected):
    assert _retry_after_seconds({"Retry-After": raw}) == expected


def test_retry_after_http_date_and_exception_do_not_preserve_headers():
    future = datetime.now(timezone.utc) + timedelta(seconds=20)
    delay = _retry_after_seconds({"Retry-After": format_datetime(future), "api-key": "private"})
    assert 18 <= delay <= 20

    class ProviderError(Exception):
        status_code = 429
        headers = {"Retry-After": "45", "api-key": "private"}

    error = _provider_exception(ProviderError("raw body with private bill"))
    assert error.code == "RATE_LIMIT"
    assert error.details == {"retry_after_seconds": 45, "retryable": True}
    assert "private" not in str(error)
    profile = _profile()
    profile.litellm_params.timeout = None
    assert build_litellm_request(_payload(), profile)["timeout"] == 60


@pytest.mark.parametrize("delay, expected_calls, expected_waits, stopped", [
    (40, 1, [], "RETRY_DEFERRED"), (2, 3, [2, 2], "PAGE_COMPLETE"),
])
def test_retry_after_respects_budget_and_checkpoint(scan_runtime, delay, expected_calls, expected_waits, stopped):
    sessions, _, view_id, tags = scan_runtime
    ledger = _seed_ledger(sessions, tags["unclassified"])
    rule = _seed_rule(sessions, view_id)
    waits, diagnostics = [], []
    clock = [0.0]

    async def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds

    transient = LlmAdapterError("safe", code="RATE_LIMIT", retryable=True, details={"retry_after_seconds": delay})
    analyzer = SequenceAnalyzer([transient, transient, lambda payload: _suggest(payload, tags["food"])])
    context = JobRunContext("tag-scan:3", datetime.now(timezone.utc), 30, 100,
                            _monotonic=lambda: clock[0], _event=diagnostics.append)
    report = asyncio.run(AutoTagScanService(sessions, analyzer, sleep=sleep).run_synthetic(
        rule, {ledger: _fixture(ledger)}, context,
    ))
    assert analyzer.calls == expected_calls and waits == expected_waits
    assert report.stopped_reason == stopped and report.failed_count == 0
    assert any(item["phase"] == "RETRY_WAIT" for item in diagnostics)
    with sessions() as db:
        row = db.get(AutoTagRule, rule)
        assert row.scan_after_ledger_id == (ledger if expected_calls == 3 else 0)
        assert row.analyzed_count == int(expected_calls == 3)


def test_pause_during_retry_wait_does_not_start_another_call_or_advance(scan_runtime):
    sessions, _, view_id, tags = scan_runtime
    ledger = _seed_ledger(sessions, tags["unclassified"])
    rule = _seed_rule(sessions, view_id)
    active = [True]

    async def sleep(_):
        active[0] = False

    analyzer = SequenceAnalyzer([LlmAdapterError("safe", code="RATE_LIMIT", retryable=True)])
    context = replace(_context(), _active=lambda: active[0])
    report = asyncio.run(AutoTagScanService(sessions, analyzer, sleep=sleep).run_synthetic(
        rule, {ledger: _fixture(ledger)}, context,
    ))
    assert analyzer.calls == 1 and report.failed_count == 0
    with sessions() as db:
        assert db.get(AutoTagRule, rule).scan_after_ledger_id == 0


def test_transport_retry_exhaustion_stops_protected_page_after_one_item(scan_runtime):
    sessions, _, view_id, tags = scan_runtime
    first = source_ledger(sessions, tags["unclassified"])
    source_ledger(sessions, tags["unclassified"])
    rule = _seed_rule(sessions, view_id)
    analyzer = SequenceAnalyzer([LlmAdapterError("safe", code="PROVIDER_UNAVAILABLE", retryable=True)] * 3)

    async def sleep(_):
        pass

    report = asyncio.run(AutoTagScanService(sessions, analyzer, sleep=sleep).run_protected(
        rule, _context(), LlmPrivacyService(),
    ))
    assert analyzer.calls == 3 and report.submitted_count == report.failed_count == 1
    assert report.stopped_reason == "PROVIDER_UNAVAILABLE"
    with sessions() as db:
        row = db.get(AutoTagRule, rule)
        assert row.scan_after_ledger_id == first and row.analyzed_count == row.failed_count == 1


def test_semantic_invalid_continues_other_items_and_provides_safe_detail(scan_runtime):
    sessions, _, view_id, tags = scan_runtime
    first = source_ledger(sessions, tags["unclassified"])
    last = source_ledger(sessions, tags["unclassified"])
    rule = _seed_rule(sessions, view_id)
    diagnostics = []
    analyzer = SequenceAnalyzer([
        LlmAdapterError("not logged", code="OUTPUT_SEMANTIC_INVALID", details={"reason_code": "ITEM_MISMATCH"}),
        lambda payload: _suggest(payload, tags["food"]),
    ])
    report = asyncio.run(AutoTagScanService(sessions, analyzer).run_protected(
        rule, replace(_context(), _event=diagnostics.append), LlmPrivacyService(),
    ))
    assert report.failed_count == 1 and report.request_count == 1
    assert any(item["detail_code"] == "ITEM_MISMATCH" and item["ledger_id"] == first for item in diagnostics)
    with sessions() as db:
        row = db.get(AutoTagRule, rule)
        assert row.scan_after_ledger_id == last and row.analyzed_count == 2 and row.failed_count == 1


def test_safe_no_call_insufficient_suggestion_and_no_data_are_distinct(scan_runtime):
    sessions, _, view_id, tags = scan_runtime
    ledgers = [source_ledger(sessions, tags["unclassified"]) for _ in range(3)]
    rule = _seed_rule(sessions, view_id)
    diagnostics = []

    class Privacy(LlmPrivacyService):
        def __init__(self):
            super().__init__()
            self.index = 0

        def build_payload(self, page, source):
            self.index += 1
            return None if self.index == 1 else super().build_payload(page, source)

    from backend.schema.llm_analysis import LlmAnalysisResult
    analyzer = SequenceAnalyzer([
        lambda payload: LlmAnalysisResult(kind="NO_SUGGESTION", item=payload.item, suggestions=[]),
        lambda payload: _suggest(payload, tags["food"]),
    ])
    service = AutoTagScanService(sessions, analyzer)
    report = asyncio.run(service.run_protected(rule, replace(_context(), _event=diagnostics.append), Privacy()))
    assert (report.no_call_count, report.insufficient_count, report.request_count) == (1, 1, 1)
    assert {item["code"] for item in diagnostics} == {"NO_CALL", "INSUFFICIENT", "SUGGESTION", "AMOUNT_BAND_UNCONFIGURED"}
    assert report.submitted_count == 2 and report.failed_count == 0
    assert asyncio.run(service.run_protected(rule, _context(), Privacy())).stopped_reason == "NO_DATA"
    with sessions() as db:
        assert db.get(AutoTagRule, rule).scan_after_ledger_id == ledgers[-1]


def test_unavailable_secret_store_is_rule_configuration_failure(scan_runtime):
    sessions, _, view_id, tags = scan_runtime
    ledger = source_ledger(sessions, tags["unclassified"])
    rule = _seed_rule(sessions, view_id)

    class Unavailable:
        def get_for_provider(self, _):
            raise ProtectedSecretStoreError("raw keyring details")

    analyzer = ConfiguredLlmAnalyzer(sessions, Unavailable())
    report = asyncio.run(AutoTagScanService(sessions, analyzer).run_protected(rule, _context(), LlmPrivacyService()))
    assert report.stopped_reason == "CONFIG_ERROR" and report.failed_count == 0
    with sessions() as db:
        assert db.get(AutoTagRule, rule).scan_after_ledger_id < ledger


def test_post_save_registration_failure_returns_warning_and_persists_rule(auto_rule_runtime, monkeypatch):
    sessions, _, view_id = auto_rule_runtime
    scheduler = JobScheduler()
    schedule = AutoTagScheduleService(sessions, scheduler, object(), real_analysis_enabled=True)

    def fail(*args, **kwargs):
        raise RuntimeError("raw registration exception")

    monkeypatch.setattr(scheduler, "register_cron", fail)
    with _client(sessions) as client:
        client.app.state.auto_tag_schedule = schedule
        response = client.post("/paam/tag/v1/auto_rule", json=_rule_payload(view_id))
        assert response.status_code == 200
        value = response.json()
        assert value["warnings"][0]["code"] == "REGISTER_FAILED"
        rule = value["body"]
        assert client.get(f'/paam/tag/v1/auto_rule/{rule["id"]}').json()["body"] == rule
        # Even an unchanged save retries registration, not the scan callback.
        retried = client.put(f'/paam/tag/v1/auto_rule/{rule["id"]}', json=_update_payload(rule))
        assert retried.json()["warnings"][0]["code"] == "REGISTER_FAILED"
    assert scheduler.snapshot().tasks[0].last_error_code == "REGISTER_FAILED"
    assert "raw registration" not in json.dumps(scheduler.diagnostics.events())
    with sessions() as db:
        assert db.scalar(select(AutoTagRule.analyzed_count)) == 0


def test_no_call_success_and_input_failure_are_partial_not_all_failed(scan_runtime):
    sessions, _, view_id, tags = scan_runtime
    rule = _seed_rule(sessions, view_id)
    schedule = AutoTagScheduleService(sessions, JobScheduler(), object(), real_analysis_enabled=True)

    class Scan:
        async def run_protected(self, *args, **kwargs):
            return ScanRunReport(
                rule, 2, 0, 0, 1, "PAGE_COMPLETE", input_failed_count=1,
                no_call_count=1, successful_count=1, last_error_code="INPUT_INVALID",
            )

    schedule._scan = Scan()
    result = asyncio.run(schedule._callback(rule)(_context()))
    assert result.result == "PARTIAL_FAILURE" and result.error_code == "INPUT_INVALID"
