import asyncio
import json
from threading import Event

import pytest
from sqlalchemy import delete, event, func, select, text, update

from backend.entity import AutoTagRule, LedgerEntry, ReviewAllocation, TagAssignmentRequest, TransactionFact
from backend.error import LlmAdapterError, TargetTagError, TargetEconomicError
from backend.mapper.auto_tag_rule_mapper import AutoTagRuleMapper
from backend.mapper.auto_tag_scan_batch_mapper import AutoTagScanBatchMapper
from backend.mapper.tag_write_mapper import TagWriteMapper
from backend.service.auto_tag_scan_service import AutoTagScanService
from backend.service.llm_privacy_service import LlmPrivacyService
from test_auto_tag_scan import (scan_runtime, _seed_ledger, _seed_rule, _fixture, _context, _suggest,
                                _run, FakeAnalyzer, SequenceAnalyzer)


def seed(runtime, count):
    sessions, _, view, tags = runtime
    ids = [_seed_ledger(sessions, tags["unclassified"]) for _ in range(count)]
    rule = _seed_rule(sessions, view)
    return ids, rule, {lid: _fixture(lid) for lid in ids}


def test_page_reads_and_single_prefix_commit_are_set_oriented(scan_runtime):
    sessions, engine, _, tags = scan_runtime
    observed = []
    in_call = False
    def sql(_conn, _cursor, statement, _parameters, _context, _many):
        assert not in_call, "SQL during the provider callback"
        if statement.lstrip().upper().startswith("SELECT"):
            observed.append("SELECT")
        elif statement == "BEGIN IMMEDIATE":
            observed.append("WRITE")
    ids, rule, fixtures = seed(scan_runtime, 1)
    def analyze(_rule, payload):
        nonlocal in_call
        in_call = True
        try:
            return _suggest(payload, tags["food"])
        finally:
            in_call = False
    event.listen(engine, "before_cursor_execute", sql)
    try:
        first = _run(AutoTagScanService(sessions, FakeAnalyzer(analyze)).run_synthetic(rule, fixtures, _context()))
        first_counts = (observed.count("SELECT"), observed.count("WRITE"))
    finally:
        event.remove(engine, "before_cursor_execute", sql)
    ids2, rule2, fixtures2 = seed(scan_runtime, 20)
    # Rule2 sees the old already-known first row too: provide its fixture and
    # compare query counts, not candidate hits or a page-sized pseudo total.
    fixtures2.update(fixtures)
    observed.clear()
    event.listen(engine, "before_cursor_execute", sql)
    try:
        second = _run(AutoTagScanService(sessions, FakeAnalyzer(analyze)).run_synthetic(rule2, fixtures2, _context()))
    finally:
        event.remove(engine, "before_cursor_execute", sql)
    assert first.request_count == 1 and second.request_count == 21
    assert first_counts == (observed.count("SELECT"), observed.count("WRITE"))
    assert observed.count("WRITE") == 1


def test_integrity_error_stops_before_bad_source_and_commits_only_earlier_prefix(scan_runtime):
    sessions, _, _, tags = scan_runtime
    ids, rule, fixtures = seed(scan_runtime, 3)
    with sessions() as db:
        db.execute(delete(TransactionFact).where(TransactionFact.id == ids[1]))
        db.commit()
    analyzer = FakeAnalyzer(lambda _, payload: _suggest(payload, tags["food"]))
    report = _run(AutoTagScanService(sessions, analyzer).run_synthetic(rule, fixtures, _context()))
    assert report.stopped_reason == "TAG_RELATION_BROKEN"
    assert len(analyzer.calls) == report.request_count == 1
    with sessions() as db:
        assert db.get(AutoTagRule, rule).scan_after_ledger_id == ids[0]
        assert db.get(AutoTagRule, rule).analyzed_count == 1
        assert list(db.scalars(select(TagAssignmentRequest.ledger_id))) == ids[:1]


def test_local_candidate_preview_rejects_orphan_positive_fact_reference(scan_runtime):
    sessions, _, view, _ = scan_runtime
    ids, rule, _fixtures = seed(scan_runtime, 1)
    with sessions() as db:
        db.execute(delete(TransactionFact).where(TransactionFact.id == ids[0]))
        db.commit()
        with pytest.raises(TargetEconomicError) as error:
            AutoTagRuleMapper(db).candidate_preview_page(rule_id=rule, rule_revision=1, view_id=view, after_id=0)
        assert error.value.code == "TAG_RELATION_BROKEN"


@pytest.mark.parametrize("error_code", ["AUTH_ERROR", "CONFIG_ERROR", "AUDIT_STORAGE_ERROR"])
def test_fatal_adapter_failure_preserves_completed_prefix_but_not_the_failed_item(scan_runtime, error_code):
    sessions, _, _, tags = scan_runtime
    ids, rule, fixtures = seed(scan_runtime, 3)
    analyzer = SequenceAnalyzer([lambda payload: _suggest(payload, tags["food"]),
                                 LlmAdapterError("safe adapter failure", code=error_code)])
    report = _run(AutoTagScanService(sessions, analyzer).run_synthetic(rule, fixtures, _context()))
    assert report.stopped_reason == error_code and report.request_count == 1
    from backend.core.feature_observability import observability
    diagnostic = observability.snapshot()
    metrics = {row["name"]: row["total"] for row in diagnostic["metrics"]}
    assert metrics["scan_pending"] == metrics["scan_failed"] == 1
    assert diagnostic["events"][-1]["code"] == error_code
    assert diagnostic["events"][-1]["level"] == "WARNING"
    assert diagnostic["events"][-1]["row_count"] == report.inspected_count
    assert analyzer.calls == 2
    with sessions() as db:
        row = db.get(AutoTagRule, rule)
        assert (row.scan_after_ledger_id, row.analyzed_count, row.failed_count) == (ids[0], 1, 0)


def test_epoch_change_after_first_result_discards_the_whole_prefix(scan_runtime):
    sessions, _, _, tags = scan_runtime
    ids, rule, fixtures = seed(scan_runtime, 3)
    def analyze(_rule, payload):
        if payload.item == fixtures[ids[1]].item:
            with sessions() as db:
                db.execute(update(AutoTagRule).where(AutoTagRule.id == rule).values(scan_epoch=2))
                db.commit()
        return _suggest(payload, tags["food"])
    report = _run(AutoTagScanService(sessions, FakeAnalyzer(analyze)).run_synthetic(rule, fixtures, _context()))
    assert report.stopped_reason == "RULE_TOKEN_CHANGED" and report.request_count == 0
    with sessions() as db:
        row = db.get(AutoTagRule, rule)
        assert (row.scan_after_ledger_id, row.analyzed_count, row.suggested_count) == (0, 0, 0)
        assert db.scalar(select(func.count()).select_from(TagAssignmentRequest)) == 0


def test_batch_rule_update_failure_rolls_back_every_request_not_only_last_item(scan_runtime):
    sessions, engine, _, tags = scan_runtime
    ids, rule, fixtures = seed(scan_runtime, 3)
    def fault(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().lower().startswith("update auto_tag_rule"):
            raise RuntimeError("mock SQL fault")
    event.listen(engine, "before_cursor_execute", fault)
    try:
        report = _run(AutoTagScanService(sessions, FakeAnalyzer(lambda _, p: _suggest(p, tags["food"])))
                      .run_synthetic(rule, fixtures, _context()))
    finally:
        event.remove(engine, "before_cursor_execute", fault)
    assert report.stopped_reason == "COMMIT_FAILED" and report.request_count == 0
    with sessions() as db:
        assert db.get(AutoTagRule, rule).scan_after_ledger_id == 0
        assert db.scalar(select(func.count()).select_from(TagAssignmentRequest)) == 0


def test_duplicate_exclusion_in_page_preview_and_locked_revalidation(scan_runtime):
    sessions, _, view, tags = scan_runtime
    ids, rule, fixtures = seed(scan_runtime, 3)
    with sessions() as db:
        db.execute(update(LedgerEntry).where(LedgerEntry.id == ids[0]).values(entry_type=3))
        db.commit()
        page = AutoTagScanBatchMapper(db).read_page(rule, limit=100)
        assert ids[0] not in page.active_ledger_ids and ids[0] not in page.protected_sources
        preview = AutoTagRuleMapper(db).candidate_preview_page(rule_id=rule, rule_revision=1, view_id=view, after_id=0)
        assert ids[0] not in preview["active_ids"]
    def analyze(_rule, payload):
        if payload.item == fixtures[ids[2]].item:
            with sessions() as db:
                db.execute(update(LedgerEntry).where(LedgerEntry.id == ids[1]).values(entry_type=3))
                db.commit()
        return _suggest(payload, tags["food"])
    report = _run(AutoTagScanService(sessions, FakeAnalyzer(analyze)).run_synthetic(rule, fixtures, _context()))
    assert report.request_count == 1 and report.skipped_count == 2
    with sessions() as db:
        assert db.get(AutoTagRule, rule).scan_after_ledger_id == ids[-1]
        assert list(db.scalars(select(TagAssignmentRequest.ledger_id))) == ids[2:]


def test_protected_page_payload_has_actual_economic_type_and_no_financial_sql_during_calls(scan_runtime):
    sessions, _, _, tags = scan_runtime
    ids, rule, _fixtures = seed(scan_runtime, 1)
    with sessions() as db:
        fact = db.get(TransactionFact, ids[0])
        fact.counterparty_name, fact.summary = "咖啡店", "早餐"
        db.commit()
    analyzer = FakeAnalyzer(lambda _, payload: _suggest(payload, tags["food"]))
    report = _run(AutoTagScanService(sessions, analyzer).run_protected(rule, _context(), LlmPrivacyService(), synthetic_only=True))
    assert report.request_count == 1
    payload = analyzer.calls[0][2]
    assert payload.economic_type == "ACCOUNT_TRANSFER"
    serialized = json.dumps(payload.model_dump(mode="json"))
    assert "fact_key" not in serialized and "ledger_id" not in serialized and "account_code" not in serialized


def test_commit_does_not_block_event_loop_cleanup_of_concurrent_read_snapshot(scan_runtime):
    sessions, _, _, tags = scan_runtime
    ids, rule, fixtures = seed(scan_runtime, 1)
    reader = sessions()
    released = []

    def analyze(_rule, payload):
        reader.execute(text("BEGIN"))
        reader.execute(select(TransactionFact.id)).all()

        def release():
            reader.close()
            released.append(True)

        # Simulate the HTTP dependency's read-snapshot cleanup. It must be able
        # to run while the SQLite writer waits for a shared read lock to leave.
        asyncio.get_running_loop().call_later(0.05, release)
        return _suggest(payload, tags["food"])

    try:
        report = _run(AutoTagScanService(sessions, FakeAnalyzer(analyze)).run_synthetic(rule, fixtures, _context()))
    finally:
        reader.close()
    assert released == [True]
    assert report.stopped_reason == "PAGE_COMPLETE" and report.request_count == 1
    with sessions() as db:
        assert db.get(AutoTagRule, rule).scan_after_ledger_id == ids[0]


def test_uncertain_committed_prefix_is_observed_not_replayed(scan_runtime, monkeypatch):
    sessions, _, _, tags = scan_runtime
    ids, rule, fixtures = seed(scan_runtime, 3)
    real_commit = TagWriteMapper.commit

    def lost_result(self):
        real_commit(self)
        raise TargetTagError(503, "fictional lost commit acknowledgement", code="RESULT_UNKNOWN")

    monkeypatch.setattr(TagWriteMapper, "commit", lost_result)
    analyzer = FakeAnalyzer(lambda _, payload: _suggest(payload, tags["food"]))
    service = AutoTagScanService(sessions, analyzer)
    report = _run(service.run_synthetic(rule, fixtures, _context()))
    assert report.stopped_reason == "RESULT_UNKNOWN" and report.request_count == 0
    with sessions() as db:
        row = db.get(AutoTagRule, rule)
        assert (row.scan_after_ledger_id, row.analyzed_count, row.suggested_count) == (ids[-1], 3, 3)
        assert db.scalar(select(func.count()).select_from(TagAssignmentRequest)) == 3
    assert _run(service.run_synthetic(rule, fixtures, _context())).stopped_reason == "NO_DATA"
    assert len(analyzer.calls) == 3


def test_cancelled_database_work_drains_started_short_transaction():
    started, release, completed = Event(), Event(), Event()

    def work():
        started.set()
        assert release.wait(2)
        completed.set()

    async def exercise():
        task = asyncio.create_task(AutoTagScanService._database_work(work))
        await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not completed.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert completed.is_set()

    _run(exercise())
