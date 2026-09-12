from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.target_database import TargetBase
from app.models.target import LedgerEntry
from app.schemas.target_ledger import TargetLedgerSummaryQuery
from app.services.target_ledger_summary_service import TargetLedgerSummaryService


def _database(tmp_path, suffix: str):
    engine = create_engine(f"sqlite:///{tmp_path / f'target-summary-{suffix}.db'}")
    TargetBase.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False)


def _entry(
    *,
    entry_id,
    occurred,
    ledger_type,
    incoming=0,
    outgoing=0,
    in_currency="CNY",
    out_currency="CNY",
    allocation_status="COMPLETE",
):
    return LedgerEntry(
        id=entry_id,
        created_time=occurred,
        updated_time=occurred,
        ledger_type=ledger_type,
        allocation_status=allocation_status,
        title=f"entry-{entry_id}",
        start_time=occurred,
        end_time=occurred,
        in_amount_value=incoming,
        in_amount_scale=2,
        in_currency_code=in_currency,
        out_amount_value=outgoing,
        out_amount_scale=2,
        out_currency_code=out_currency,
        input_hash=str(entry_id).zfill(64),
        projection_version=1,
    )


def test_target_summary_keeps_integer_business_and_currency_semantics(tmp_path):
    _, sessions = _database(tmp_path, "semantics")
    started = datetime(2026, 9, 14, 9)
    with sessions() as db:
        db.add_all([
            _entry(entry_id=1, occurred=started, ledger_type="INCOME", incoming=1000),
            _entry(entry_id=2, occurred=started, ledger_type="EXPENSE", outgoing=1200),
            _entry(
                entry_id=3,
                occurred=started,
                ledger_type="REFUND",
                incoming=200,
                outgoing=500,
                allocation_status="PARTIAL",
            ),
            _entry(entry_id=4, occurred=started, ledger_type="AA", incoming=1000, outgoing=1200),
            _entry(entry_id=5, occurred=started, ledger_type="TRANSFER", incoming=999, outgoing=1000),
            _entry(entry_id=6, occurred=started, ledger_type="LOAN_BORROW", incoming=1000, outgoing=1000),
            _entry(
                entry_id=7,
                occurred=started,
                ledger_type="FX_EXCHANGE",
                incoming=100,
                outgoing=700,
                in_currency="USD",
                out_currency="CNY",
            ),
            _entry(
                entry_id=8,
                occurred=started + timedelta(days=1),
                ledger_type="EXPENSE",
                outgoing=250,
                in_currency="USD",
                out_currency="USD",
            ),
        ])
        db.commit()

        summary = TargetLedgerSummaryService(db).summary(TargetLedgerSummaryQuery())
        assert (summary.entry_count, summary.provisional_count) == (8, 1)
        totals = {item.currency_code: item for item in summary.totals}
        assert (
            totals["CNY"].amount_scale,
            totals["CNY"].income_value,
            totals["CNY"].expense_value,
            totals["CNY"].refund_offset_value,
            totals["CNY"].net_value,
        ) == (2, 1000, 1901, 200, -701)
        assert (
            totals["USD"].income_value,
            totals["USD"].expense_value,
            totals["USD"].net_value,
        ) == (0, 250, -250)
        fx = {
            (item.currency_code, item.in_amount_value, item.out_amount_value, item.nettable)
            for item in summary.activities
            if item.ledger_type == "FX_EXCHANGE"
        }
        assert fx == {("CNY", 0, 700, False), ("USD", 100, 0, False)}

        first_day = TargetLedgerSummaryService(db).summary(TargetLedgerSummaryQuery(
            date_from=date(2026, 9, 14),
            date_to=date(2026, 9, 14),
        ))
        assert first_day.entry_count == 7
        assert {item.currency_code for item in first_day.totals} == {"CNY", "USD"}
        assert next(item for item in first_day.totals if item.currency_code == "USD").net_value == 0


def _summary_select_count(tmp_path, count: int) -> int:
    engine, sessions = _database(tmp_path, f"count-{count}")
    started = datetime(2026, 9, 14, 10)
    with sessions() as db:
        db.add_all([
            _entry(
                entry_id=index + 1,
                occurred=started + timedelta(minutes=index),
                ledger_type="EXPENSE",
                outgoing=100,
            )
            for index in range(count)
        ])
        db.commit()
    selects = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        with sessions() as db:
            summary = TargetLedgerSummaryService(db).summary(TargetLedgerSummaryQuery())
            assert summary.entry_count == count
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    return selects


def test_target_summary_select_count_does_not_grow_with_entries(tmp_path):
    assert _summary_select_count(tmp_path, 10) == _summary_select_count(tmp_path, 100) == 2
