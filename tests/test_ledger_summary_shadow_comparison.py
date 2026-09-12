from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import sessionmaker

from app.database import Base, Bill
from app.models.target import LedgerEntry
from app.services.ledger_summary_comparison_service import (
    LedgerSummaryShadowComparisonService,
)


def _database(tmp_path, suffix: str):
    engine = create_engine(f"sqlite:///{tmp_path / f'summary-comparison-{suffix}.db'}")
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False)


def _seed_matching(sessions, count: int):
    started = datetime(2026, 9, 15, 9)
    with sessions() as db:
        for index in range(count):
            occurred = started + timedelta(minutes=index)
            db.add(Bill(
                occurred_at=occurred,
                merchant=f"merchant-{index}",
                note="",
                amount=-1,
                currency="CNY",
                category="Unclassified",
                tags="",
                aggregate_excluded=False,
            ))
            db.add(LedgerEntry(
                id=index + 1,
                created_time=occurred,
                updated_time=occurred,
                ledger_type="EXPENSE",
                allocation_status="DEFAULT",
                title=f"merchant-{index}",
                start_time=occurred,
                end_time=occurred,
                in_amount_value=0,
                in_amount_scale=2,
                in_currency_code="CNY",
                out_amount_value=100,
                out_amount_scale=2,
                out_currency_code="CNY",
                input_hash=str(index + 1).zfill(64),
                projection_version=1,
            ))
        db.commit()


def test_summary_comparison_matches_and_reports_drift(tmp_path):
    _, sessions = _database(tmp_path, "match")
    _seed_matching(sessions, 10)
    with sessions() as db:
        report = LedgerSummaryShadowComparisonService(db).compare()
        assert report.matched
        assert report.comparable
        assert report.legacy == report.target
        assert (report.legacy_entry_count, report.target_entry_count) == (10, 10)

        db.execute(update(LedgerEntry).where(LedgerEntry.id == 1).values(out_amount_value=99))
        db.commit()
        drift = LedgerSummaryShadowComparisonService(db).compare()
        assert not drift.matched
        assert any("expense_value" in difference for difference in drift.differences)


def _comparison_select_count(tmp_path, count: int) -> int:
    engine, sessions = _database(tmp_path, f"count-{count}")
    _seed_matching(sessions, count)
    selects = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        with sessions() as db:
            assert LedgerSummaryShadowComparisonService(db).compare().matched
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    return selects


def test_summary_comparison_select_count_does_not_grow_with_rows(tmp_path):
    assert _comparison_select_count(tmp_path, 10) == _comparison_select_count(tmp_path, 100) == 9
