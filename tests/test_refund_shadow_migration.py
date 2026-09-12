from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.database import (
    Base,
    Bill,
    RefundAllocation,
    RefundAllocationAudit,
    RefundDesignation,
    RefundNatureAudit,
)
from app.models.target import ReviewCase, ReviewCaseBill, ReviewHistory
from app.services.refund_migration_service import (
    REFUND_CASE_START,
    RefundReviewShadowMigrationService,
)
from app.services.target_migration_service import FactShadowMigrationService


def _database(tmp_path, suffix: str):
    engine = create_engine(f"sqlite:///{tmp_path / f'refund-shadow-{suffix}.db'}")
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False)


def _allocation_audit(
    allocation_id: int,
    *,
    action: str,
    before_status,
    after_status: str,
    amount: float,
    created_at: datetime,
    reverses_audit_id=None,
    idempotency_key=None,
):
    return RefundAllocationAudit(
        allocation_id=allocation_id,
        action=action,
        actor="local-user",
        reason=action,
        before_state=json.dumps({"status": before_status}),
        after_state=json.dumps({"status": after_status, "amount": amount}),
        reverses_audit_id=reverses_audit_id,
        idempotency_key=idempotency_key,
        request_payload=json.dumps({"action": action}),
        created_at=created_at,
    )


def _seed_partial_refund_with_revoked_allocation(sessions):
    started = datetime(2026, 9, 6, 9)
    with sessions() as db:
        refund = Bill(
            occurred_at=started,
            merchant="Store refund",
            note="",
            amount=100,
            currency="CNY",
        )
        first_expense = Bill(
            occurred_at=started - timedelta(days=10),
            merchant="Store order A",
            note="",
            amount=-100,
            currency="CNY",
        )
        second_expense = Bill(
            occurred_at=started - timedelta(days=9),
            merchant="Store order B",
            note="",
            amount=-40,
            currency="CNY",
        )
        db.add_all([refund, first_expense, second_expense])
        db.flush()
        db.add(RefundDesignation(bill_id=refund.id, created_at=started))
        db.add(RefundNatureAudit(
            bill_id=refund.id,
            action="refund",
            reason="provider marked refund",
            actor="local-user",
            before_nature="ordinary",
            after_nature="refund",
            idempotency_key="nature-refund",
            request_payload='{"nature":"refund"}',
            created_at=started,
        ))
        first = RefundAllocation(
            refund_bill_id=refund.id,
            expense_bill_id=first_expense.id,
            amount=60,
            status="confirmed",
            idempotency_key="allocation-60",
            request_payload='{"amount":60}',
            created_at=started + timedelta(minutes=1),
            revoked_at=None,
        )
        second = RefundAllocation(
            refund_bill_id=refund.id,
            expense_bill_id=second_expense.id,
            amount=40,
            status="revoked",
            idempotency_key="allocation-40",
            request_payload='{"amount":40}',
            created_at=started + timedelta(minutes=2),
            revoked_at=started + timedelta(minutes=3),
        )
        db.add_all([first, second])
        db.flush()
        first_confirm = _allocation_audit(
            first.id,
            action="confirm",
            before_status=None,
            after_status="confirmed",
            amount=60,
            created_at=started + timedelta(minutes=1),
        )
        second_confirm = _allocation_audit(
            second.id,
            action="confirm",
            before_status=None,
            after_status="confirmed",
            amount=40,
            created_at=started + timedelta(minutes=2),
        )
        db.add_all([first_confirm, second_confirm])
        db.flush()
        db.add(_allocation_audit(
            second.id,
            action="revoke",
            before_status="confirmed",
            after_status="revoked",
            amount=40,
            created_at=started + timedelta(minutes=3),
            reverses_audit_id=second_confirm.id,
            idempotency_key="allocation-40-undo",
        ))
        db.commit()
        return refund.id, first_expense.id, second_expense.id


def test_refund_shadow_unifies_nature_allocations_and_reversal_history(tmp_path):
    _, sessions = _database(tmp_path, "partial")
    refund_id, first_expense_id, _ = _seed_partial_refund_with_revoked_allocation(sessions)
    with sessions() as db:
        assert FactShadowMigrationService(db).backfill_and_compare().matched
        legacy_counts = (
            db.query(RefundAllocation).count(),
            db.query(RefundAllocationAudit).count(),
            db.query(RefundDesignation).count(),
            db.query(RefundNatureAudit).count(),
        )
        report = RefundReviewShadowMigrationService(db).backfill_and_compare()
        assert report.matched
        case_id = REFUND_CASE_START + refund_id
        case = db.get(ReviewCase, case_id)
        assert (
            case.review_type,
            case.status,
            case.allocation_status,
            case.version,
        ) == ("REFUND", "CONFIRMED", "PARTIAL", 4)
        assert json.loads(case.result_json) == {
            "confirmed_allocation_count": 1,
            "legacy_nature": "refund",
            "total_allocation_count": 2,
        }
        lines = db.scalars(
            select(ReviewCaseBill)
            .where(ReviewCaseBill.case_id == case_id)
            .order_by(ReviewCaseBill.id)
        ).all()
        assert [(line.bill_id, line.role, line.amount_value) for line in lines] == [
            (refund_id, "REFUND_RECEIVED", 10000),
            (first_expense_id, "REFUND_EXPENSE", 6000),
        ]
        history = db.scalars(
            select(ReviewHistory)
            .where(ReviewHistory.case_id == case_id)
            .order_by(ReviewHistory.version)
        ).all()
        assert [row.operation for row in history] == [
            "CREATE", "CONFIRM", "CONFIRM", "REVOKE",
        ]
        assert history[-1].reverses_history_id == history[-2].id
        assert history[-1].before_json == history[-2].after_json
        assert (
            db.query(RefundAllocation).count(),
            db.query(RefundAllocationAudit).count(),
            db.query(RefundDesignation).count(),
            db.query(RefundNatureAudit).count(),
        ) == legacy_counts

        repeated = RefundReviewShadowMigrationService(db).backfill_and_compare()
        assert repeated.matched
        assert repeated.review_case.inserted_count == 0
        assert repeated.review_case_bill.inserted_count == 0
        assert repeated.review_history.inserted_count == 0


def _seed_refunds(sessions, count: int):
    started = datetime(2026, 9, 7, 9)
    with sessions() as db:
        for index in range(count):
            refund = Bill(
                occurred_at=started + timedelta(minutes=index),
                merchant=f"refund-{index}",
                note="",
                amount=100,
                currency="CNY",
            )
            expense = Bill(
                occurred_at=started - timedelta(days=1, minutes=index),
                merchant=f"expense-{index}",
                note="",
                amount=-100,
                currency="CNY",
            )
            db.add_all([refund, expense])
            db.flush()
            db.add(RefundDesignation(bill_id=refund.id, created_at=started))
            db.add(RefundNatureAudit(
                bill_id=refund.id,
                action="refund",
                reason="refund",
                actor="local-user",
                before_nature="ordinary",
                after_nature="refund",
                idempotency_key=f"nature-{index}",
                request_payload='{"nature":"refund"}',
                created_at=started,
            ))
            allocation = RefundAllocation(
                refund_bill_id=refund.id,
                expense_bill_id=expense.id,
                amount=100,
                status="confirmed",
                idempotency_key=f"allocation-{index}",
                request_payload='{"amount":100}',
                created_at=started + timedelta(seconds=1),
                revoked_at=None,
            )
            db.add(allocation)
            db.flush()
            db.add(_allocation_audit(
                allocation.id,
                action="confirm",
                before_status=None,
                after_status="confirmed",
                amount=100,
                created_at=started + timedelta(seconds=1),
            ))
        db.commit()


def _refund_select_count(tmp_path, count: int) -> int:
    engine, sessions = _database(tmp_path, f"count-{count}")
    _seed_refunds(sessions, count)
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
            assert RefundReviewShadowMigrationService(db).backfill_and_compare().matched
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    return selects


def test_refund_shadow_select_count_does_not_grow_with_rows(tmp_path):
    assert _refund_select_count(tmp_path, 10) == _refund_select_count(tmp_path, 100) == 11
