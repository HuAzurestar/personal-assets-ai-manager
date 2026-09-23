from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker

from backend.core.job_scheduler import JobRunContext, JobScheduler
from backend.core.target_database import init_target_db
from backend.entity import AutoTagRule
from backend.error import LlmAdapterError
from backend.mapper.auto_tag_rule_mapper import AutoTagRuleMapper
from backend.mapper.auto_tag_scan_mapper import (
    ScanCommitResult,
    ScanPage,
    ScanTarget,
    ScanToken,
)
from backend.schema.llm_analysis import (
    LlmAmountDisclosure,
    LlmCandidate,
    ProtectedLlmAnalysisInput,
)
from backend.service.auto_tag_schedule_service import AutoTagScheduleService
from backend.service.auto_tag_scan_service import AutoTagScanService

NOW = datetime(2026, 9, 22, 8, tzinfo=timezone.utc)


class FakeSecretStore:
    def is_configured(self, model_id):
        return model_id == 9

    def set(self, model_id, secret):
        del model_id, secret

    def delete(self, model_id):
        del model_id

    def get_for_provider(self, model_id):
        return "test-key" if model_id == 9 else None


def _runtime(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'schedule.db'}",
        connect_args={"check_same_thread": False},
    )
    sessions = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    init_target_db(bind=engine)
    with sessions() as db:
        mapper = AutoTagRuleMapper(db)
        mapper.begin_write()
        rule_id = mapper.create(
            name="Scheduled rule",
            view_id=1,
            method_config={
                "schema_version": 1,
                "model_id": 9,
                "prompt": "Choose one tag",
            },
            enabled=1,
            cron="*/5 * * * *",
            amount_mode=1,
            now=NOW,
        )
        mapper.commit()
    return engine, sessions, rule_id


def test_persisted_enabled_rules_are_restored_and_ticks_use_shared_fifo(tmp_path):
    engine, sessions, rule_id = _runtime(tmp_path)
    scheduler = JobScheduler()
    service = AutoTagScheduleService(sessions, scheduler, FakeSecretStore())
    called = asyncio.Event()
    contexts = []

    class FakeScan:
        async def run_protected(self, actual_rule_id, context, privacy):
            contexts.append((actual_rule_id, context.task_key, privacy))
            called.set()

    service._scan = FakeScan()

    async def scenario():
        service.register_persisted()
        registered = scheduler.snapshot().tasks
        assert [item.task_key for item in registered] == [f"tag-scan:{rule_id}"]
        await scheduler.start()
        try:
            assert await scheduler.notify(f"tag-scan:{rule_id}") is True
            await asyncio.wait_for(called.wait(), timeout=1)
            assert contexts[0][0:2] == (rule_id, f"tag-scan:{rule_id}")
            assert scheduler.snapshot().tasks[0].next_run_at is not None
        finally:
            await scheduler.shutdown()

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_disabling_a_rule_removes_its_registration(tmp_path):
    engine, sessions, rule_id = _runtime(tmp_path)
    scheduler = JobScheduler()
    service = AutoTagScheduleService(sessions, scheduler, FakeSecretStore())
    try:
        service.register_persisted()
        assert len(scheduler.snapshot().tasks) == 1
        with sessions() as db:
            db.execute(update(AutoTagRule).where(
                AutoTagRule.id == rule_id,
            ).values(enabled=0))
            db.commit()
        service.sync_rule(rule_id)
        assert scheduler.snapshot().tasks == ()
    finally:
        engine.dispose()


def test_protected_scan_stops_after_first_provider_wide_failure():
    class FailingAnalyzer:
        calls = 0

        async def analyze(self, payload, *, rule_id, model_id):
            del payload, rule_id, model_id
            self.calls += 1
            raise LlmAdapterError(
                "provider unavailable",
                code="PROVIDER_UNAVAILABLE",
                retryable=False,
            )

    analyzer = FailingAnalyzer()
    service = AutoTagScanService(lambda: None, analyzer)
    page = ScanPage(
        token=ScanToken(1, 1, 1, 0),
        enabled=True,
        view_active=True,
        model_enabled=True,
        view_id=1,
        model_id=9,
        prompt="Choose one tag",
        amount_mode=1,
        ledger_ids=(1, 2),
        active_ledger_ids=frozenset({1, 2}),
        active_tag_states={1: ("unclassified",), 2: ("unclassified",)},
        existing_request_ids=frozenset(),
        targets=(ScanTarget(2, "Food"),),
    )
    service._read_page = lambda rule_id, limit: page
    committed = []

    def commit(token, ledger_id, kind, suggestions):
        committed.append((ledger_id, kind, suggestions))
        return ScanCommitResult("COMMITTED", "ANALYSIS_COMMITTED")

    service._commit = commit
    payload = ProtectedLlmAnalysisInput(
        source="PROTECTED_LEDGER",
        item="item_1",
        direction="OUT",
        merchant="Cafe",
        summary="Breakfast",
        rule_prompt="Choose one tag",
        amount=LlmAmountDisclosure(
            mode="BAND",
            currency_code="CNY",
            band_code="CNY:0:3000",
            band_label="[0,3000)",
        ),
        candidates=[LlmCandidate(tag_id=2, name="Food")],
    )
    context = JobRunContext(
        task_key="tag-scan:1",
        started_at=NOW,
        deadline_monotonic=100,
        page_limit=100,
        _monotonic=lambda: 0,
    )

    report = asyncio.run(service._run(
        1,
        context,
        lambda page, ledger_id: payload,
        stop_on_provider_error=True,
    ))

    assert analyzer.calls == 1
    assert committed == [(1, "ITEM_FAILURE", ())]
    assert report.stopped_reason == "PROVIDER_UNAVAILABLE"
    assert report.failed_count == 1
