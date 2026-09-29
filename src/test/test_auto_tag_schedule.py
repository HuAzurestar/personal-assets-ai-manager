from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker

from backend.core.job_scheduler import JobRunContext, JobScheduler
from backend.core.target_database import init_target_db
from backend.entity import AutoTagRule, TransactionFact
from backend.error import LlmAdapterError
from backend.mapper.auto_tag_rule_mapper import AutoTagRuleMapper
from backend.mapper.setting_mapper import SettingMapper
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
from backend.service.auto_tag_scan_service import AutoTagScanService, ScanRunReport
from backend.service.auto_tag_schedule_service import AutoTagScheduleService
from backend.service.schedule_status_service import ScheduleStatusService

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


def test_default_mode_never_registers_tag_scans(tmp_path):
    engine, sessions, rule_id = _runtime(tmp_path)
    scheduler = JobScheduler()
    service = AutoTagScheduleService(sessions, scheduler, FakeSecretStore())
    try:
        service.register_persisted()
        service.sync_rule(rule_id)
        assert scheduler.snapshot().tasks == ()
        assert ScheduleStatusService(
            scheduler, sessions, synthetic_acceptance_enabled=False,
        ).get().tag_scan_guard == "DISABLED"
    finally:
        engine.dispose()


def test_global_scan_setting_synchronizes_jobs_and_survives_restart(tmp_path):
    engine, sessions, rule_id = _runtime(tmp_path)
    scheduler = JobScheduler()
    service = AutoTagScheduleService(
        sessions, scheduler, FakeSecretStore(), real_analysis_enabled=True,
    )
    try:
        service.register_persisted()
        assert [task.task_key for task in scheduler.snapshot().tasks] == [f"tag-scan:{rule_id}"]
        with sessions() as db:
            mapper = SettingMapper(db)
            mapper.begin_write()
            mapper.save({"schema_version": 1, "automation": {"scan_enabled": False}}, NOW)
            mapper.commit()
        service.sync_enabled()
        assert scheduler.snapshot().tasks == ()
        assert ScheduleStatusService(
            scheduler, sessions, synthetic_acceptance_enabled=False,
            real_analysis_enabled=True,
        ).get().tag_scan_guard == "DISABLED"
        restarted = AutoTagScheduleService(
            sessions, JobScheduler(), FakeSecretStore(), real_analysis_enabled=True,
        )
        restarted.register_persisted()
        assert restarted._scheduler.snapshot().tasks == ()
        with sessions() as db:
            mapper = SettingMapper(db)
            mapper.begin_write()
            mapper.save({"schema_version": 1, "automation": {"scan_enabled": True}}, NOW)
            mapper.commit()
        service.sync_enabled()
        assert [task.task_key for task in scheduler.snapshot().tasks] == [f"tag-scan:{rule_id}"]
    finally:
        engine.dispose()


def test_acceptance_mode_rejects_database_with_ordinary_fact(tmp_path):
    engine, sessions, rule_id = _runtime(tmp_path)
    scheduler = JobScheduler()
    service = AutoTagScheduleService(
        sessions, scheduler, FakeSecretStore(), synthetic_acceptance_enabled=True,
    )
    try:
        with sessions() as db:
            db.add(TransactionFact(
                fact_key="ordinary-bill",
                occurred_time=NOW,
                cash_direction=2,
                amount=100,
                currency_code="CNY",
                account_code="fixture",
                counterparty_name="Do not send",
                counterparty_account_ref="",
                summary="Private summary",
                created_time=NOW,
                updated_time=NOW,
            ))
            db.commit()
        service.register_persisted()
        service.sync_rule(rule_id)
        assert scheduler.snapshot().tasks == ()
        assert ScheduleStatusService(
            scheduler, sessions, synthetic_acceptance_enabled=True,
        ).get().tag_scan_guard == "NON_SYNTHETIC_FACT"
    finally:
        engine.dispose()


def test_new_ordinary_fact_blocks_already_registered_tick(tmp_path):
    engine, sessions, rule_id = _runtime(tmp_path)
    scheduler = JobScheduler()
    service = AutoTagScheduleService(
        sessions, scheduler, FakeSecretStore(), synthetic_acceptance_enabled=True,
    )

    class ForbiddenScan:
        async def run_protected(self, *args, **kwargs):
            raise AssertionError("ordinary facts must never reach a model")

    service._scan = ForbiddenScan()
    service.register_persisted()
    with sessions() as db:
        db.add(TransactionFact(
            fact_key="ordinary-bill",
            occurred_time=NOW,
            cash_direction=2,
            amount=100,
            currency_code="CNY",
            account_code="fixture",
            counterparty_name="Do not send",
            counterparty_account_ref="",
            summary="Private summary",
            created_time=NOW,
            updated_time=NOW,
        ))
        db.commit()

    async def scenario():
        await scheduler.start()
        try:
            assert await scheduler.notify(f"tag-scan:{rule_id}") is True
            for _ in range(100):
                task = scheduler.snapshot().tasks[0]
                if task.last_result is not None:
                    break
                await asyncio.sleep(0.01)
            assert task.last_result == "FAILED"
            assert task.last_error_code == "ACCEPTANCE_DATABASE_REQUIRED"
            assert task.queue_state == "PAUSED"
            assert task.next_run_at is None
            assert await scheduler.notify(f"tag-scan:{rule_id}") is False
            assert ScheduleStatusService(
                scheduler, sessions, synthetic_acceptance_enabled=True,
            ).get().tag_scan_guard == "NON_SYNTHETIC_FACT"
        finally:
            await scheduler.shutdown()

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_persisted_enabled_rules_are_restored_and_ticks_use_shared_fifo(tmp_path):
    engine, sessions, rule_id = _runtime(tmp_path)
    scheduler = JobScheduler()
    service = AutoTagScheduleService(
        sessions, scheduler, FakeSecretStore(), synthetic_acceptance_enabled=True,
    )
    called = asyncio.Event()
    contexts = []

    class FakeScan:
        async def run_protected(self, actual_rule_id, context, privacy, *, synthetic_only=False):
            assert synthetic_only is True
            contexts.append((actual_rule_id, context.task_key, privacy))
            called.set()
            return ScanRunReport(actual_rule_id, 0, 0, 0, 0, "PAGE_COMPLETE")

    service._scan = FakeScan()

    async def scenario():
        service.register_persisted()
        assert ScheduleStatusService(
            scheduler, sessions, synthetic_acceptance_enabled=True,
        ).get().tag_scan_guard == "SYNTHETIC_READY"
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


@pytest.mark.parametrize(
    ("report_counts", "reason", "input_failed", "expected_result", "expected_code"),
    [
        ((1, 1, 0, 1), "OUTPUT_JSON_INVALID", 0, "FAILED", "OUTPUT_JSON_INVALID"),
        ((2, 2, 1, 1), "PAGE_COMPLETE", 0, "PARTIAL_FAILURE", "ITEM_FAILURE"),
        ((0, 0, 0, 0), "MODEL_DISABLED", 0, "FAILED", "MODEL_DISABLED"),
        ((0, 0, 0, 0), "RULE_DISABLED", 0, "FAILED", "RULE_DISABLED"),
        ((0, 0, 0, 0), "VIEW_INACTIVE", 0, "FAILED", "VIEW_INACTIVE"),
        ((0, 0, 0, 0), "SOFT_BUDGET_EXHAUSTED", 0, "PARTIAL_FAILURE", "SOFT_BUDGET_EXHAUSTED"),
        ((2, 2, 2, 0), "SOFT_BUDGET_EXHAUSTED", 0, "PARTIAL_FAILURE", "SOFT_BUDGET_EXHAUSTED"),
        ((1, 1, 0, 1), "SOFT_BUDGET_EXHAUSTED", 0, "PARTIAL_FAILURE", "SOFT_BUDGET_EXHAUSTED"),
        ((2, 1, 1, 1), "PAGE_COMPLETE", 1, "PARTIAL_FAILURE", "ITEM_FAILURE"),
        ((1, 0, 0, 1), "PAGE_COMPLETE", 1, "FAILED", "ITEM_FAILURE"),
    ],
)
def test_scan_outcome_is_visible_in_shared_scheduler(
    tmp_path, report_counts, reason, input_failed, expected_result, expected_code,
):
    engine, sessions, rule_id = _runtime(tmp_path)
    scheduler = JobScheduler()
    service = AutoTagScheduleService(
        sessions, scheduler, FakeSecretStore(), synthetic_acceptance_enabled=True,
    )

    class FakeScan:
        async def run_protected(self, actual_rule_id, context, privacy, *, synthetic_only=False):
            assert synthetic_only is True
            del context, privacy
            return ScanRunReport(
                actual_rule_id, *report_counts, reason, input_failed_count=input_failed,
                successful_count=report_counts[1] - report_counts[3] + input_failed,
                last_error_code=reason if reason.startswith("OUTPUT_") else None,
            )

    service._scan = FakeScan()

    async def scenario():
        service.register_persisted()
        await scheduler.start()
        try:
            assert await scheduler.notify(f"tag-scan:{rule_id}") is True
            for _ in range(100):
                task = scheduler.snapshot().tasks[0]
                if task.last_result is not None:
                    break
                await asyncio.sleep(0.01)
            assert task.last_result == expected_result
            assert task.last_error_code == expected_code
        finally:
            await scheduler.shutdown()

    try:
        asyncio.run(scenario())
    finally:
        engine.dispose()


def test_disabling_a_rule_removes_its_registration(tmp_path):
    engine, sessions, rule_id = _runtime(tmp_path)
    scheduler = JobScheduler()
    service = AutoTagScheduleService(
        sessions, scheduler, FakeSecretStore(), synthetic_acceptance_enabled=True,
    )
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
