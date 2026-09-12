from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.database import AccountRevision, Base, Bill
from app.models.target import ReviewCase, ReviewCaseBill, ReviewHistory
from app.services.account_migration_service import (
    ACCOUNT_CASE_START,
    AccountReviewShadowMigrationService,
)
from app.services.target_migration_service import FactShadowMigrationService


def _database(tmp_path, suffix: str):
    engine = create_engine(f"sqlite:///{tmp_path / f'account-shadow-{suffix}.db'}")
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False)


def _seed_confirm_then_undo(sessions):
    started = datetime(2026, 9, 8, 9)
    with sessions() as db:
        bill = Bill(
            occurred_at=started,
            merchant="Card purchase",
            note="",
            amount=-20,
            currency="CNY",
            account_name="wallet-a",
        )
        db.add(bill)
        db.flush()
        confirmation = AccountRevision(
            bill_id=bill.id,
            before_account="wallet-a",
            after_account="wallet-b",
            action="confirm",
            actor="local-user",
            reason="correct account",
            reverses_revision_id=None,
            undone=True,
            undone_at=started + timedelta(minutes=2),
            idempotency_key="account-confirm",
            request_payload='{"account_name":"wallet-b"}',
            created_at=started + timedelta(minutes=1),
        )
        db.add(confirmation)
        db.flush()
        db.add(AccountRevision(
            bill_id=bill.id,
            before_account="wallet-b",
            after_account="wallet-a",
            action="undo",
            actor="local-user",
            reason="restore original",
            reverses_revision_id=confirmation.id,
            undone=False,
            undone_at=None,
            idempotency_key="account-undo",
            request_payload='{"reason":"restore original"}',
            created_at=started + timedelta(minutes=2),
        ))
        db.commit()
        return bill.id


def test_account_shadow_preserves_current_value_and_exact_reversal(tmp_path):
    _, sessions = _database(tmp_path, "undo")
    bill_id = _seed_confirm_then_undo(sessions)
    with sessions() as db:
        assert FactShadowMigrationService(db).backfill_and_compare().matched
        source_count = db.query(AccountRevision).count()
        report = AccountReviewShadowMigrationService(db).backfill_and_compare()
        assert report.matched
        case_id = ACCOUNT_CASE_START + bill_id
        case = db.get(ReviewCase, case_id)
        assert (
            case.review_type,
            case.status,
            case.allocation_status,
            case.version,
        ) == ("ACCOUNT", "REVOKED", "COMPLETE", 2)
        assert json.loads(case.result_json) == {"account_name": "wallet-a"}
        line = db.scalar(
            select(ReviewCaseBill).where(ReviewCaseBill.case_id == case_id)
        )
        assert (line.bill_id, line.role, line.amount_value) == (bill_id, "ACCOUNT", 2000)
        history = db.scalars(
            select(ReviewHistory)
            .where(ReviewHistory.case_id == case_id)
            .order_by(ReviewHistory.version)
        ).all()
        assert [row.operation for row in history] == ["CREATE", "RESTORE"]
        assert history[1].reverses_history_id == history[0].id
        assert history[1].before_json == history[0].after_json
        assert json.loads(history[1].after_json)["result"] == {
            "account_name": "wallet-a",
        }
        assert db.query(AccountRevision).count() == source_count

        repeated = AccountReviewShadowMigrationService(db).backfill_and_compare()
        assert repeated.matched
        assert repeated.review_case.inserted_count == 0
        assert repeated.review_case_bill.inserted_count == 0
        assert repeated.review_history.inserted_count == 0


def _seed_account_revisions(sessions, count: int):
    started = datetime(2026, 9, 9, 9)
    with sessions() as db:
        for index in range(count):
            bill = Bill(
                occurred_at=started + timedelta(minutes=index),
                merchant=f"merchant-{index}",
                note="",
                amount=-10,
                currency="CNY",
                account_name="wallet-b",
            )
            db.add(bill)
            db.flush()
            db.add(AccountRevision(
                bill_id=bill.id,
                before_account="wallet-a",
                after_account="wallet-b",
                action="confirm",
                actor="local-user",
                reason="correct account",
                reverses_revision_id=None,
                undone=False,
                undone_at=None,
                idempotency_key=f"account-{index}",
                request_payload='{"account_name":"wallet-b"}',
                created_at=started,
            ))
        db.commit()


def _account_select_count(tmp_path, count: int) -> int:
    engine, sessions = _database(tmp_path, f"count-{count}")
    _seed_account_revisions(sessions, count)
    with sessions() as db:
        assert FactShadowMigrationService(db).backfill_and_compare().matched
    selects = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        with sessions() as db:
            assert AccountReviewShadowMigrationService(db).backfill_and_compare().matched
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    return selects


def test_account_shadow_select_count_does_not_grow_with_rows(tmp_path):
    assert _account_select_count(tmp_path, 10) == _account_select_count(tmp_path, 100) == 9

