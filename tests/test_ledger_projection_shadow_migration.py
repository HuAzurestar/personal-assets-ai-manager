from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.target_database import TargetBase
from app.models.target import (
    BillFact,
    LedgerEntry,
    LedgerEntrySource,
    LedgerEntryTag,
    ReviewCase,
    ReviewCaseBill,
    TargetTag,
    TargetTagView,
)
from app.services.ledger_projection_migration_service import (
    LedgerProjectionShadowMigrationService,
)


def _database(tmp_path, suffix: str):
    engine = create_engine(f"sqlite:///{tmp_path / f'ledger-projection-{suffix}.db'}")
    Base.metadata.create_all(bind=engine)
    TargetBase.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False)


def _fact(*, fact_id, occurred, direction, amount, currency="CNY", scale=2):
    return BillFact(
        id=fact_id,
        created_time=occurred,
        updated_time=occurred,
        fact_key=f"fact-{fact_id}",
        occurred_time=occurred,
        cash_direction=direction,
        amount_value=amount,
        amount_scale=scale,
        currency_code=currency,
        account_code=f"account-{fact_id}",
        counterparty=f"party-{fact_id}",
        summary="",
    )


def _case(*, case_id, occurred, review_type, title, result_json="{}"):
    return ReviewCase(
        id=case_id,
        created_time=occurred,
        updated_time=occurred,
        review_type=review_type,
        status="CONFIRMED",
        allocation_status="COMPLETE",
        version=1,
        title=title,
        result_json=result_json,
    )


def _line(*, line_id, case_id, fact, role):
    return ReviewCaseBill(
        id=line_id,
        created_time=fact.occurred_time,
        updated_time=fact.occurred_time,
        case_id=case_id,
        bill_id=fact.id,
        role=role,
        party="",
        amount_value=fact.amount_value,
        amount_scale=fact.amount_scale,
        currency_code=fact.currency_code,
    )


def _seed_projection_inputs(sessions):
    started = datetime(2026, 9, 13, 9)
    normal = _fact(
        fact_id=1,
        occurred=started,
        direction="OUT",
        amount=1000,
        currency="USD",
    )
    transfer_out = _fact(fact_id=2, occurred=started, direction="OUT", amount=1000)
    transfer_in = _fact(
        fact_id=3,
        occurred=started + timedelta(minutes=1),
        direction="IN",
        amount=1000,
    )
    retained = _fact(fact_id=4, occurred=started, direction="OUT", amount=500)
    excluded = _fact(fact_id=5, occurred=started, direction="OUT", amount=500)
    fx_out = _fact(fact_id=6, occurred=started, direction="OUT", amount=700)
    fx_in = _fact(
        fact_id=7,
        occurred=started + timedelta(minutes=1),
        direction="IN",
        amount=100,
        currency="USD",
    )
    with sessions() as db:
        facts = [normal, transfer_out, transfer_in, retained, excluded, fx_out, fx_in]
        db.add_all(facts)
        db.add_all([
            _case(case_id=100, occurred=started, review_type="TRANSFER", title="Own transfer"),
            _case(case_id=101, occurred=started, review_type="DUPLICATE", title="Duplicate"),
            _case(case_id=102, occurred=started, review_type="FX_EXCHANGE", title="FX"),
            _case(
                case_id=103,
                occurred=started,
                review_type="ACCOUNT",
                title="Account",
                result_json='{"account_name":"corrected-account"}',
            ),
            _case(
                case_id=104,
                occurred=started,
                review_type="TAG",
                title="Tags",
                result_json='{"tag_state":{"category":"dining"}}',
            ),
        ])
        db.add(TargetTagView(
            id=1,
            created_time=started,
            updated_time=started,
            name="Category",
            system_name="category",
            status="ACTIVE",
        ))
        db.add_all([
            TargetTag(
                id=1,
                created_time=started,
                updated_time=started,
                view_id=1,
                name="Unclassified",
                system_name="unclassified",
                status="ACTIVE",
            ),
            TargetTag(
                id=2,
                created_time=started,
                updated_time=started,
                view_id=1,
                name="Dining",
                system_name="dining",
                status="ACTIVE",
            ),
        ])
        db.add_all([
            _line(line_id=1001, case_id=100, fact=transfer_out, role="TRANSFER_OUT"),
            _line(line_id=1002, case_id=100, fact=transfer_in, role="TRANSFER_IN"),
            _line(line_id=1011, case_id=101, fact=retained, role="DUPLICATE_RETAINED"),
            _line(line_id=1012, case_id=101, fact=excluded, role="DUPLICATE_EXCLUDED"),
            _line(line_id=1021, case_id=102, fact=fx_out, role="TRANSFER_OUT"),
            _line(line_id=1022, case_id=102, fact=fx_in, role="TRANSFER_IN"),
            _line(line_id=1031, case_id=103, fact=normal, role="ACCOUNT"),
            _line(line_id=1041, case_id=104, fact=normal, role="TAG"),
        ])
        db.commit()


def test_ledger_projection_combines_cash_legs_and_deduplicates(tmp_path):
    _, sessions = _database(tmp_path, "semantics")
    _seed_projection_inputs(sessions)
    with sessions() as db:
        source_counts = (
            db.query(BillFact).count(),
            db.query(ReviewCase).count(),
            db.query(ReviewCaseBill).count(),
        )
        report = LedgerProjectionShadowMigrationService(db).backfill_and_compare()
        assert report.matched, report.blockers
        entries = {
            entry.id: entry
            for entry in db.scalars(select(LedgerEntry).order_by(LedgerEntry.id)).all()
        }
        assert set(entries) == {1, 2, 4, 6}
        assert (
            entries[1].ledger_type,
            entries[1].in_amount_value,
            entries[1].out_amount_value,
            entries[1].out_currency_code,
            entries[1].out_account_code,
        ) == ("EXPENSE", 0, 1000, "USD", "corrected-account")
        assert (
            entries[2].ledger_type,
            entries[2].in_amount_value,
            entries[2].out_amount_value,
            entries[2].in_account_code,
            entries[2].out_account_code,
        ) == ("TRANSFER", 1000, 1000, "account-3", "account-2")
        assert (
            entries[4].ledger_type,
            entries[4].out_amount_value,
        ) == ("EXPENSE", 500)
        assert (
            entries[6].ledger_type,
            entries[6].in_amount_value,
            entries[6].in_currency_code,
            entries[6].out_amount_value,
            entries[6].out_currency_code,
        ) == ("FX_EXCHANGE", 100, "USD", 700, "CNY")
        assert all(len(entry.input_hash) == 64 for entry in entries.values())
        sources = db.scalars(select(LedgerEntrySource).order_by(LedgerEntrySource.id)).all()
        assert len(sources) == 12
        assert len({(source.source_kind, source.source_id) for source in sources}) == 12
        assert next(
            source for source in sources
            if source.source_kind == "REVIEW_CASE" and source.source_id == 104
        ).ledger_id == 1
        assignments = db.scalars(
            select(LedgerEntryTag).order_by(LedgerEntryTag.ledger_id)
        ).all()
        assert [(assignment.ledger_id, assignment.tag_id) for assignment in assignments] == [
            (1, 2), (2, 1), (4, 1), (6, 1),
        ]
        assert (
            db.query(BillFact).count(),
            db.query(ReviewCase).count(),
            db.query(ReviewCaseBill).count(),
        ) == source_counts

        repeated = LedgerProjectionShadowMigrationService(db).backfill_and_compare()
        assert repeated.matched
        assert repeated.ledger_entry.inserted_count == 0
        assert repeated.ledger_entry_source.inserted_count == 0
        assert repeated.ledger_entry_tag.inserted_count == 0

        db.execute(update(LedgerEntry).where(LedgerEntry.id == 1).values(title="tampered"))
        db.commit()
        drift = LedgerProjectionShadowMigrationService(db).backfill_and_compare()
        assert not drift.matched
        assert drift.ledger_entry.mismatched_ids == [1]
        assert db.get(LedgerEntry, 1).title == "tampered"


def _seed_count_inputs(sessions, count: int):
    started = datetime(2026, 9, 13, 10)
    with sessions() as db:
        facts = [
            _fact(
                fact_id=index + 1,
                occurred=started + timedelta(minutes=index),
                direction="OUT" if index != 1 else "IN",
                amount=1000,
            )
            for index in range(count)
        ]
        db.add_all(facts)
        db.add(_case(
            case_id=100,
            occurred=started,
            review_type="TRANSFER",
            title="Transfer",
        ))
        db.add_all([
            _line(line_id=1001, case_id=100, fact=facts[0], role="TRANSFER_OUT"),
            _line(line_id=1002, case_id=100, fact=facts[1], role="TRANSFER_IN"),
        ])
        db.commit()


def _projection_select_count(tmp_path, count: int) -> int:
    engine, sessions = _database(tmp_path, f"count-{count}")
    _seed_count_inputs(sessions, count)
    selects = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        with sessions() as db:
            assert LedgerProjectionShadowMigrationService(db).backfill_and_compare().matched
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    return selects


def test_ledger_projection_select_count_does_not_grow_with_facts(tmp_path):
    assert _projection_select_count(tmp_path, 10) == _projection_select_count(tmp_path, 100) == 11


def test_ledger_projection_blocks_conflicting_tags_in_one_component(tmp_path):
    _, sessions = _database(tmp_path, "tag-conflict")
    started = datetime(2026, 9, 13, 11)
    outgoing = _fact(fact_id=1, occurred=started, direction="OUT", amount=1000)
    incoming = _fact(fact_id=2, occurred=started, direction="IN", amount=1000)
    with sessions() as db:
        db.add_all([outgoing, incoming])
        db.add(TargetTagView(
            id=1,
            created_time=started,
            updated_time=started,
            name="Category",
            system_name="category",
            status="ACTIVE",
        ))
        db.add_all([
            TargetTag(id=1, created_time=started, updated_time=started, view_id=1, name="Unclassified", system_name="unclassified", status="ACTIVE"),
            TargetTag(id=2, created_time=started, updated_time=started, view_id=1, name="Dining", system_name="dining", status="ACTIVE"),
            TargetTag(id=3, created_time=started, updated_time=started, view_id=1, name="Travel", system_name="travel", status="ACTIVE"),
        ])
        db.add_all([
            _case(case_id=100, occurred=started, review_type="TRANSFER", title="Transfer"),
            _case(case_id=101, occurred=started, review_type="TAG", title="Dining", result_json='{"tag_state":{"category":"dining"}}'),
            _case(case_id=102, occurred=started, review_type="TAG", title="Travel", result_json='{"tag_state":{"category":"travel"}}'),
        ])
        db.add_all([
            _line(line_id=1001, case_id=100, fact=outgoing, role="TRANSFER_OUT"),
            _line(line_id=1002, case_id=100, fact=incoming, role="TRANSFER_IN"),
            _line(line_id=1011, case_id=101, fact=outgoing, role="TAG"),
            _line(line_id=1021, case_id=102, fact=incoming, role="TAG"),
        ])
        db.commit()

        report = LedgerProjectionShadowMigrationService(db).backfill_and_compare()
        assert not report.matched
        assert any("conflicting tags for view category" in item for item in report.blockers)
