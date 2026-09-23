from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.orm import sessionmaker

from backend.core.job_scheduler import JobRunContext, JobScheduler
from backend.core.target_database import init_target_db
from backend.entity import (
    MAX_COUNTER_VALUE,
    TAG_REQUEST_STATUS_CANCELLED,
    AutoTagRule,
    LedgerEntry,
    LedgerEntryTag,
    ReviewAllocation,
    ReviewCase,
    TagAssignmentRequest,
    TargetTag,
    TransactionFact,
)
from backend.error import LlmAdapterError
from backend.mapper.auto_tag_rule_mapper import AutoTagRuleMapper
from backend.mapper.setting_mapper import SettingMapper
from backend.mapper.target_tag_mapper import TargetTagMapper
from backend.schema.auto_tag_scan import SyntheticTagScanFixture
from backend.schema.llm_analysis import (
    LlmAmountDisclosure,
    LlmAnalysisResult,
    LlmResolvedSuggestion,
)
from backend.schema.target_review import (
    TargetEconomicReviewCreateRequest,
    TargetReviewTransitionRequest,
)
from backend.service.auto_tag_scan_service import AutoTagScanService
from backend.service.target_economic_service import TargetEconomicService

NOW = datetime(2026, 9, 22, 8, tzinfo=timezone.utc)


@pytest.fixture
def scan_runtime(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'auto-tag-scan.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    init_target_db(bind=engine)
    with sessions() as db:
        tags = TargetTagMapper(db)
        tags.begin_write()
        view_id = tags.create_view("Category", "category", NOW)
        tags.create_tag(view_id, "Food", "food", NOW)
        tags.create_tag(view_id, "Travel", "travel", NOW)
        dictionary = tags.view(view_id)
        tags.commit()
        tag_ids = {tag.system_name: tag.id for tag in dictionary.tags}
        settings = SettingMapper(db)
        settings.begin_write()
        settings.save({
            "schema_version": 1,
            "automation": {
                "models": [{
                    "id": 9,
                    "name": "Synthetic model",
                    "enabled": True,
                    "litellm_params": {
                        "model": "openai/synthetic",
                        "api_base": "https://example.test/v1",
                    },
                }],
                "disclosure": {},
            },
        }, NOW)
        settings.commit()
    try:
        yield sessions, engine, view_id, tag_ids
    finally:
        engine.dispose()


class FakeAnalyzer:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []

    async def analyze(self, payload, *, rule_id, model_id):
        self.calls.append((rule_id, model_id, payload))
        value = self.behavior(rule_id, payload)
        if isinstance(value, Exception):
            raise value
        return value


class SequenceAnalyzer:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = 0

    async def analyze(self, payload, *, rule_id, model_id):
        del rule_id, model_id
        self.calls += 1
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value(payload)


def _seed_rule(sessions, view_id: int, *, amount_mode: int = 1) -> int:
    with sessions() as db:
        mapper = AutoTagRuleMapper(db)
        mapper.begin_write()
        rule_id = mapper.create(
            name="Synthetic rule",
            view_id=view_id,
            method_config={
                "schema_version": 1,
                "model_id": 9,
                "prompt": "Choose matching tags",
            },
            enabled=1,
            cron="*/5 * * * *",
            amount_mode=amount_mode,
            now=NOW,
        )
        mapper.commit()
        return rule_id


def _seed_ledger(
    sessions,
    tag_id: int,
    *,
    active: bool = True,
) -> int:
    with sessions() as db:
        ledger = LedgerEntry(
            entry_type=1,
            entry_direction=1,
            amount=12_300,
            currency_code="CNY",
            account_code="fixture",
            counterparty_account_ref="synthetic",
            occurred_time=NOW,
            created_time=NOW,
            updated_time=NOW,
        )
        db.add(ledger)
        db.flush()
        review = ReviewCase(
            behavior_type=1,
            status=0 if active else 1,
            title=f"case-{ledger.id}",
            created_time=NOW,
            updated_time=NOW,
        )
        db.add(review)
        db.flush()
        db.add(ReviewAllocation(
            review_case_id=review.id,
            transaction_fact_id=ledger.id,
            ledger_entry_id=ledger.id,
            amount=12_300,
            currency_code="CNY",
            created_time=NOW,
            updated_time=NOW,
        ))
        db.add(LedgerEntryTag(
            ledger_id=ledger.id,
            tag_id=tag_id,
            created_time=NOW,
            updated_time=NOW,
        ))
        db.commit()
        return ledger.id


def _fixture(ledger_id: int) -> SyntheticTagScanFixture:
    return SyntheticTagScanFixture(
        source="SYNTHETIC_FIXTURE",
        ledger_id=ledger_id,
        item=f"fixture-{ledger_id}",
        direction="OUT",
        merchant=f"merchant-{ledger_id}",
        summary="auditable synthetic record",
        amount=LlmAmountDisclosure(
            mode="BAND",
            currency_code="CNY",
            band_code="100_500",
            band_label="100-500",
        ),
    )


def _context(*, page_limit: int = 100) -> JobRunContext:
    return JobRunContext(
        task_key="tag-scan:test",
        started_at=NOW,
        deadline_monotonic=10.0,
        page_limit=page_limit,
        _monotonic=lambda: 0.0,
    )


def _suggest(payload, *tag_ids: int) -> LlmAnalysisResult:
    names = {candidate.tag_id: candidate.name for candidate in payload.candidates}
    return LlmAnalysisResult(
        kind="SUGGESTED",
        item=payload.item,
        suggestions=[
            LlmResolvedSuggestion(
                tag_id=tag_id,
                tag_name=names[tag_id],
                reason=f"synthetic reason {tag_id}",
            )
            for tag_id in tag_ids
        ],
    )


def _run(coroutine):
    return asyncio.run(coroutine)


def test_multi_rule_page_commits_suggestions_failures_and_insufficient(
    scan_runtime,
):
    sessions, _, view_id, tag_ids = scan_runtime
    ledger_ids = [
        _seed_ledger(sessions, tag_ids["unclassified"])
        for _ in range(3)
    ]
    first_rule = _seed_rule(sessions, view_id)
    second_rule = _seed_rule(sessions, view_id)

    def behavior(_, payload):
        ledger_id = int(payload.item.rsplit("-", 1)[1])
        if ledger_id == ledger_ids[0]:
            return _suggest(payload, tag_ids["food"], tag_ids["travel"])
        if ledger_id == ledger_ids[1]:
            return LlmAnalysisResult(
                kind="NO_SUGGESTION",
                item=payload.item,
                suggestions=[],
            )
        return LlmAdapterError(
            "temporary provider failure",
            code="PROVIDER_UNAVAILABLE",
            retryable=True,
        )

    analyzer = FakeAnalyzer(behavior)
    service = AutoTagScanService(sessions, analyzer)
    fixtures = {ledger_id: _fixture(ledger_id) for ledger_id in ledger_ids}

    first = _run(service.run_synthetic(first_rule, fixtures, _context()))
    second = _run(service.run_synthetic(second_rule, fixtures, _context()))

    assert first.stopped_reason == second.stopped_reason == "PAGE_COMPLETE"
    assert first.submitted_count == second.submitted_count == 3
    assert first.request_count == second.request_count == 2
    assert first.failed_count == second.failed_count == 1
    assert len(analyzer.calls) == 10
    for _, model_id, payload in analyzer.calls:
        assert model_id == 9
        assert payload.source == "SYNTHETIC_FIXTURE"
        assert [item.tag_id for item in payload.candidates] == [
            tag_ids["food"],
            tag_ids["travel"],
        ]

    with sessions() as db:
        rules = db.execute(select(
            AutoTagRule.id,
            AutoTagRule.scan_after_ledger_id,
            AutoTagRule.analyzed_count,
            AutoTagRule.failed_count,
            AutoTagRule.suggested_count,
        ).order_by(AutoTagRule.id)).all()
        assert rules == [
            (first_rule, ledger_ids[-1], 3, 1, 2),
            (second_rule, ledger_ids[-1], 3, 1, 2),
        ]
        assert db.scalar(select(func.count(TagAssignmentRequest.id))) == 4


def test_rule_level_auth_error_does_not_advance_or_count(scan_runtime):
    sessions, _, view_id, tag_ids = scan_runtime
    ledger_id = _seed_ledger(sessions, tag_ids["unclassified"])
    rule_id = _seed_rule(sessions, view_id)
    analyzer = FakeAnalyzer(lambda *_: LlmAdapterError(
        "credential rejected",
        code="AUTH_ERROR",
    ))

    report = _run(AutoTagScanService(sessions, analyzer).run_synthetic(
        rule_id,
        {ledger_id: _fixture(ledger_id)},
        _context(),
    ))

    assert report.stopped_reason == "AUTH_ERROR"
    with sessions() as db:
        rule = db.get(AutoTagRule, rule_id)
        assert rule.scan_after_ledger_id == 0
        assert rule.analyzed_count == rule.failed_count == rule.suggested_count == 0
        assert db.scalar(select(func.count(TagAssignmentRequest.id))) == 0


def test_missing_active_targets_blocks_rule_without_advancing(scan_runtime):
    sessions, _, view_id, tag_ids = scan_runtime
    ledger_id = _seed_ledger(sessions, tag_ids["unclassified"])
    rule_id = _seed_rule(sessions, view_id)
    with sessions() as db:
        db.execute(update(TargetTag).where(
            TargetTag.id.in_([tag_ids["food"], tag_ids["travel"]]),
        ).values(status="ARCHIVED"))
        db.commit()
    analyzer = FakeAnalyzer(
        lambda _, payload: _suggest(payload, tag_ids["food"])
    )

    report = _run(AutoTagScanService(sessions, analyzer).run_synthetic(
        rule_id,
        {ledger_id: _fixture(ledger_id)},
        _context(),
    ))

    assert report.stopped_reason == "NO_ACTIVE_TARGETS"
    assert analyzer.calls == []
    with sessions() as db:
        rule = db.get(AutoTagRule, rule_id)
        assert rule.scan_after_ledger_id == 0
        assert rule.analyzed_count == 0


def test_restart_resumes_after_last_atomic_checkpoint(scan_runtime):
    sessions, _, view_id, tag_ids = scan_runtime
    ledger_ids = [
        _seed_ledger(sessions, tag_ids["unclassified"])
        for _ in range(2)
    ]
    rule_id = _seed_rule(sessions, view_id)
    fixtures = {ledger_id: _fixture(ledger_id) for ledger_id in ledger_ids}
    first_analyzer = FakeAnalyzer(
        lambda _, payload: _suggest(payload, tag_ids["food"])
    )

    first = _run(AutoTagScanService(sessions, first_analyzer).run_synthetic(
        rule_id,
        fixtures,
        _context(page_limit=1),
    ))
    restarted_analyzer = FakeAnalyzer(
        lambda _, payload: _suggest(payload, tag_ids["travel"])
    )
    resumed = _run(AutoTagScanService(
        sessions,
        restarted_analyzer,
    ).run_synthetic(rule_id, fixtures, _context()))

    assert first.inspected_count == first.submitted_count == 1
    assert resumed.inspected_count == resumed.submitted_count == 1
    assert restarted_analyzer.calls[0][2].item == f"fixture-{ledger_ids[1]}"
    with sessions() as db:
        rule = db.get(AutoTagRule, rule_id)
        assert rule.scan_after_ledger_id == ledger_ids[1]
        assert rule.analyzed_count == rule.suggested_count == 2
        assert db.scalar(select(func.count(TagAssignmentRequest.id))) == 2


def test_retryable_item_uses_at_most_three_attempts_and_counts_once(
    scan_runtime,
):
    sessions, _, view_id, tag_ids = scan_runtime
    ledger_id = _seed_ledger(sessions, tag_ids["unclassified"])
    rule_id = _seed_rule(sessions, view_id)
    def transient():
        return LlmAdapterError(
            "temporary provider failure",
            code="PROVIDER_UNAVAILABLE",
            retryable=True,
        )
    analyzer = SequenceAnalyzer([
        transient(),
        transient(),
        lambda payload: _suggest(payload, tag_ids["food"]),
    ])

    report = _run(AutoTagScanService(sessions, analyzer).run_synthetic(
        rule_id,
        {ledger_id: _fixture(ledger_id)},
        _context(),
    ))

    assert analyzer.calls == 3
    assert report.submitted_count == 1
    assert report.failed_count == 0
    with sessions() as db:
        rule = db.get(AutoTagRule, rule_id)
        assert rule.analyzed_count == 1
        assert rule.failed_count == 0
        assert rule.suggested_count == 1


def test_old_worker_result_is_discarded_after_epoch_change(scan_runtime):
    sessions, _, view_id, tag_ids = scan_runtime
    ledger_id = _seed_ledger(sessions, tag_ids["unclassified"])
    rule_id = _seed_rule(sessions, view_id)

    def invalidate(_, payload):
        with sessions() as db:
            db.execute(update(AutoTagRule).where(
                AutoTagRule.id == rule_id,
            ).values(scan_epoch=AutoTagRule.scan_epoch + 1))
            db.commit()
        return _suggest(payload, tag_ids["food"])

    report = _run(AutoTagScanService(
        sessions,
        FakeAnalyzer(invalidate),
    ).run_synthetic(
        rule_id,
        {ledger_id: _fixture(ledger_id)},
        _context(),
    ))

    assert report.stopped_reason == "RULE_TOKEN_CHANGED"
    with sessions() as db:
        rule = db.get(AutoTagRule, rule_id)
        assert rule.scan_epoch == 2
        assert rule.scan_after_ledger_id == 0
        assert rule.analyzed_count == 0
        assert db.scalar(select(func.count(TagAssignmentRequest.id))) == 0


def test_atomic_commit_rolls_back_request_when_rule_update_fails(scan_runtime):
    sessions, engine, view_id, tag_ids = scan_runtime
    ledger_id = _seed_ledger(sessions, tag_ids["unclassified"])
    rule_id = _seed_rule(sessions, view_id)
    insert_seen = False

    def fail_rule_update(_, __, statement, ___, ____, _____):
        nonlocal insert_seen
        normalized = statement.lower().lstrip()
        if normalized.startswith("insert into tag_assignment_request"):
            insert_seen = True
        if insert_seen and normalized.startswith("update auto_tag_rule"):
            raise RuntimeError("injected rule update failure")

    event.listen(engine, "before_cursor_execute", fail_rule_update)
    try:
        with pytest.raises(RuntimeError, match="injected rule update failure"):
            _run(AutoTagScanService(
                sessions,
                FakeAnalyzer(lambda _, payload: _suggest(
                    payload, tag_ids["food"]
                )),
            ).run_synthetic(
                rule_id,
                {ledger_id: _fixture(ledger_id)},
                _context(),
            ))
    finally:
        event.remove(engine, "before_cursor_execute", fail_rule_update)

    assert insert_seen is True
    with sessions() as db:
        rule = db.get(AutoTagRule, rule_id)
        assert rule.scan_after_ledger_id == 0
        assert rule.analyzed_count == rule.suggested_count == 0
        assert db.scalar(select(func.count(TagAssignmentRequest.id))) == 0


def test_duplicate_trigger_skips_existing_request_without_duplicate(scan_runtime):
    sessions, _, view_id, tag_ids = scan_runtime
    ledger_id = _seed_ledger(sessions, tag_ids["unclassified"])
    rule_id = _seed_rule(sessions, view_id)
    analyzer = FakeAnalyzer(
        lambda _, payload: _suggest(payload, tag_ids["food"])
    )
    service = AutoTagScanService(sessions, analyzer)
    fixtures = {ledger_id: _fixture(ledger_id)}

    _run(service.run_synthetic(rule_id, fixtures, _context()))
    with sessions() as db:
        db.execute(update(AutoTagRule).where(
            AutoTagRule.id == rule_id,
        ).values(scan_after_ledger_id=0))
        db.commit()
    repeated = _run(service.run_synthetic(rule_id, fixtures, _context()))

    assert repeated.submitted_count == 0
    with sessions() as db:
        rule = db.get(AutoTagRule, rule_id)
        assert rule.scan_after_ledger_id == ledger_id
        assert rule.analyzed_count == rule.suggested_count == 1
        assert db.scalar(select(func.count(TagAssignmentRequest.id))) == 1


def test_counter_overflow_aborts_whole_item_commit(scan_runtime):
    sessions, _, view_id, tag_ids = scan_runtime
    ledger_id = _seed_ledger(sessions, tag_ids["unclassified"])
    rule_id = _seed_rule(sessions, view_id)
    with sessions() as db:
        db.execute(update(AutoTagRule).where(
            AutoTagRule.id == rule_id,
        ).values(analyzed_count=MAX_COUNTER_VALUE))
        db.commit()

    with pytest.raises(OverflowError, match="analyzed_count"):
        _run(AutoTagScanService(
            sessions,
            FakeAnalyzer(
                lambda _, payload: _suggest(payload, tag_ids["food"])
            ),
        ).run_synthetic(
            rule_id,
            {ledger_id: _fixture(ledger_id)},
            _context(),
        ))

    with sessions() as db:
        rule = db.get(AutoTagRule, rule_id)
        assert rule.scan_after_ledger_id == 0
        assert rule.analyzed_count == MAX_COUNTER_VALUE
        assert rule.suggested_count == 0
        assert db.scalar(select(func.count(TagAssignmentRequest.id))) == 0


def test_active_review_semantics_ignore_physical_inactive_ledger(scan_runtime):
    sessions, _, view_id, tag_ids = scan_runtime
    inactive_id = _seed_ledger(
        sessions,
        tag_ids["unclassified"],
        active=False,
    )
    active_id = _seed_ledger(sessions, tag_ids["unclassified"])
    rule_id = _seed_rule(sessions, view_id)
    analyzer = FakeAnalyzer(
        lambda _, payload: _suggest(payload, tag_ids["food"])
    )
    service = AutoTagScanService(sessions, analyzer)
    fixtures = {
        inactive_id: _fixture(inactive_id),
        active_id: _fixture(active_id),
    }

    report = _run(service.run_synthetic(rule_id, fixtures, _context()))

    assert report.inspected_count == 2
    assert report.submitted_count == 1
    assert len(analyzer.calls) == 1
    with sessions() as db:
        rule = db.get(AutoTagRule, rule_id)
        assert rule.scan_after_ledger_id == active_id
        assert db.scalar(select(func.count(TagAssignmentRequest.id))) == 1


def test_scheduler_executes_synthetic_callback(scan_runtime):
    sessions, _, view_id, tag_ids = scan_runtime
    ledger_id = _seed_ledger(sessions, tag_ids["unclassified"])
    rule_id = _seed_rule(sessions, view_id)
    service = AutoTagScanService(
        sessions,
        FakeAnalyzer(lambda _, payload: _suggest(payload, tag_ids["food"])),
    )

    async def scenario():
        scheduler = JobScheduler()
        scheduler.register_interval(
            f"tag-scan:{rule_id}",
            seconds=3600,
            callback=service.callback(rule_id, {ledger_id: _fixture(ledger_id)}),
        )
        await scheduler.start()
        try:
            assert await scheduler.notify(f"tag-scan:{rule_id}") is True
            for _ in range(100):
                if scheduler.snapshot().tasks[0].last_result is not None:
                    break
                await asyncio.sleep(0.01)
            assert scheduler.snapshot().tasks[0].last_result == "COMPLETED"
        finally:
            await scheduler.shutdown()

    _run(scenario())
    with sessions() as db:
        assert db.get(AutoTagRule, rule_id).scan_after_ledger_id == ledger_id
        assert db.scalar(select(func.count(TagAssignmentRequest.id))) == 1


def test_cron_tick_creates_request_without_manual_notification(scan_runtime):
    sessions, _, view_id, tag_ids = scan_runtime
    ledger_id = _seed_ledger(sessions, tag_ids["unclassified"])
    rule_id = _seed_rule(sessions, view_id)
    service = AutoTagScanService(
        sessions,
        FakeAnalyzer(lambda _, payload: _suggest(payload, tag_ids["food"])),
    )

    async def scenario():
        scheduler = JobScheduler()
        scheduler.register_cron(
            f"tag-scan:{rule_id}",
            expression="*/1 * * * * *",
            callback=service.callback(rule_id, {ledger_id: _fixture(ledger_id)}),
        )
        await scheduler.start()
        try:
            for _ in range(50):
                with sessions() as db:
                    count = db.scalar(select(func.count(TagAssignmentRequest.id)))
                if count == 1 and scheduler.snapshot().tasks[0].last_result == "COMPLETED":
                    break
                await asyncio.sleep(0.1)
            assert count == 1
            assert scheduler.snapshot().tasks[0].last_result == "COMPLETED"
        finally:
            await scheduler.shutdown()

    _run(scenario())
    with sessions() as db:
        assert db.get(AutoTagRule, rule_id).scan_after_ledger_id == ledger_id


def test_review_revoke_and_restore_invalidate_scan_and_pending_request(
    scan_runtime,
):
    sessions, _, view_id, tag_ids = scan_runtime
    with sessions() as db:
        fact = TransactionFact(
            fact_key="synthetic-revoke-fact",
            occurred_time=NOW,
            cash_direction=2,
            amount=5_000,
            currency_code="CNY",
            account_code="fixture",
            counterparty_name="synthetic",
            counterparty_account_ref="",
            summary="fixture",
            created_time=NOW,
            updated_time=NOW,
        )
        db.add(fact)
        db.flush()
        economics = TargetEconomicService(db)
        economics.ensure_defaults([fact.id], commit=True)
        review = economics.create(TargetEconomicReviewCreateRequest(
            behavior_type=1,
            title="Synthetic review",
            economics=[{
                "client_key": "purchase",
                "economic_type": "TRANSACTION",
            }],
            allocations=[{
                "fact_id": fact.id,
                "economic_key": "purchase",
                "amount": 5_000,
            }],
            idempotency_key="synthetic-create",
        ))
        ledger_id = review.allocations[0].ledger_entry_id
        assert db.scalar(select(LedgerEntryTag.tag_id).where(
            LedgerEntryTag.ledger_id == ledger_id,
        )) == tag_ids["unclassified"]

    rule_id = _seed_rule(sessions, view_id)
    with sessions() as db:
        db.execute(update(AutoTagRule).where(
            AutoTagRule.id == rule_id,
        ).values(scan_after_ledger_id=ledger_id))
        db.add(TagAssignmentRequest(
            rule_id=rule_id,
            rule_revision=1,
            ledger_id=ledger_id,
            view_id=view_id,
            proposed_tag_id=tag_ids["food"],
            status=1,
            reason_summary="synthetic pending",
            created_time=NOW,
            updated_time=NOW,
        ))
        db.commit()

    with sessions() as db:
        revoked = TargetEconomicService(db).revoke(
            review.id,
            TargetReviewTransitionRequest(idempotency_key="synthetic-revoke"),
        )
        assert revoked.status == 1
    with sessions() as db:
        rule = db.get(AutoTagRule, rule_id)
        request = db.scalar(select(TagAssignmentRequest).where(
            TagAssignmentRequest.rule_id == rule_id,
        ))
        assert rule.scan_epoch == 2
        assert rule.scan_after_ledger_id == ledger_id - 1
        assert request.status == TAG_REQUEST_STATUS_CANCELLED

    with sessions() as db:
        restored = TargetEconomicService(db).restore(
            review.id,
            TargetReviewTransitionRequest(idempotency_key="synthetic-restore"),
        )
        assert restored.status == 0
    with sessions() as db:
        rule = db.get(AutoTagRule, rule_id)
        assert rule.scan_epoch == 3
        assert rule.rule_revision == 1
