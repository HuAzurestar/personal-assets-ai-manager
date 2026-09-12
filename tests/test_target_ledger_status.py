from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.database import Base, Bill
from app.target_database import TargetBase
from app.models.target import (
    BillFact,
    LedgerEntry,
    LedgerEntrySource,
    LedgerEntryTag,
    TargetTag,
    TargetTagView,
)
from app.services.target_ledger_status_service import TargetLedgerStatusService


def _seed(tmp_path, count: int):
    engine = create_engine(f"sqlite:///{tmp_path / f'target-status-{count}.db'}")
    Base.metadata.create_all(bind=engine)
    TargetBase.metadata.create_all(bind=engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    occurred = datetime(2026, 9, 4, 9)
    with sessions() as db:
        db.add(TargetTagView(
            id=1,
            created_time=occurred,
            updated_time=occurred,
            name="消费类别",
            system_name="category",
            status="ACTIVE",
        ))
        db.add(TargetTag(
            id=1,
            created_time=occurred,
            updated_time=occurred,
            view_id=1,
            name="未分类",
            system_name="unclassified",
            status="ACTIVE",
        ))
        for index in range(count):
            item_id = index + 1
            item_time = occurred + timedelta(minutes=index)
            db.add(Bill(
                id=item_id,
                occurred_at=item_time,
                merchant=f"merchant-{item_id}",
                note="",
                amount=-1,
                currency="CNY",
                category="未分类",
                tags="",
                account_name="wallet",
                aggregate_excluded=False,
            ))
            db.add(BillFact(
                id=item_id,
                created_time=item_time,
                updated_time=item_time,
                fact_key=f"fact-{item_id}",
                occurred_time=item_time,
                cash_direction="OUT",
                amount_value=100,
                amount_scale=2,
                currency_code="CNY",
                account_code="wallet",
                counterparty=f"merchant-{item_id}",
                summary="",
            ))
            db.add(LedgerEntry(
                id=item_id,
                created_time=item_time,
                updated_time=item_time,
                ledger_type="EXPENSE",
                allocation_status="DEFAULT",
                title=f"merchant-{item_id}",
                start_time=item_time,
                end_time=item_time,
                in_amount_value=0,
                in_amount_scale=2,
                in_currency_code="CNY",
                out_amount_value=100,
                out_amount_scale=2,
                out_currency_code="CNY",
                in_account_code="UNKNOWN",
                out_account_code="wallet",
                input_hash=str(item_id).zfill(64),
                projection_version=2,
            ))
            db.add(LedgerEntrySource(
                id=item_id << 1,
                ledger_id=item_id,
                source_kind="BILL_FACT",
                source_id=item_id,
            ))
            db.add(LedgerEntryTag(
                id=(item_id << 20) | 1,
                ledger_id=item_id,
                tag_id=1,
            ))
        db.commit()
    return engine, sessions


def _status_select_count(tmp_path, count: int):
    engine, sessions = _seed(tmp_path, count)
    selects = 0

    def listener(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(engine, "before_cursor_execute", listener)
    try:
        with sessions() as db:
            status = TargetLedgerStatusService(db).status()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    assert status.ready, status.blockers
    assert status.legacy_bill_count == status.fact_count == status.fact_source_count == count
    assert status.ledger_tag_count == count
    return selects


def test_target_ledger_status_is_a_fixed_query_dual_read_gate(tmp_path):
    assert _status_select_count(tmp_path, 10) == _status_select_count(tmp_path, 100) == 10


def test_target_ledger_status_blocks_empty_projection_when_legacy_has_data(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'target-status-empty.db'}")
    Base.metadata.create_all(bind=engine)
    TargetBase.metadata.create_all(bind=engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    with sessions() as db:
        db.add(Bill(
            occurred_at=datetime(2026, 9, 4, 10),
            merchant="legacy-only",
            note="",
            amount=-1,
            currency="CNY",
        ))
        db.commit()
        status = TargetLedgerStatusService(db).status()
    assert not status.ready
    assert "ledger projection is empty while legacy bills exist" in status.blockers
