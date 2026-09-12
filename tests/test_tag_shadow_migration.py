from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, Bill, TagAudit
from app.target_database import TargetBase
from app.models.target import ReviewCase, ReviewCaseBill, ReviewHistory
from app.services.tag_migration_service import (
    TAG_CASE_START,
    TagReviewShadowMigrationService,
)
from app.services.target_migration_service import FactShadowMigrationService


def _database(tmp_path, suffix: str):
    engine = create_engine(f"sqlite:///{tmp_path / f'tag-shadow-{suffix}.db'}")
    Base.metadata.create_all(bind=engine)
    TargetBase.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False)


def _audit(*, bill_id, created_at, category, state, before_category, before_state, **values):
    return TagAudit(
        bill_id=bill_id,
        category=category,
        tags=values.pop("tags", category),
        tag_state_json=json.dumps(state, sort_keys=True),
        strategy=values.pop("strategy", "manual"),
        confidence=values.pop("confidence", 1.0),
        provider=values.pop("provider", "manual"),
        superseded=values.pop("superseded"),
        action=values.pop("action"),
        actor="local-user",
        reason=values.pop("reason", ""),
        before_state_json=json.dumps(before_state, sort_keys=True),
        before_category=before_category,
        reverses_audit_id=values.pop("reverses_audit_id", None),
        undone=values.pop("undone", False),
        undone_at=values.pop("undone_at", None),
        idempotency_key=values.pop("idempotency_key"),
        request_payload=values.pop("request_payload", "{}"),
        created_at=created_at,
        **values,
    )


def _seed_decision_history(sessions):
    started = datetime(2026, 9, 10, 9)
    unclassified = {"category": "unclassified", "scenario": "unclassified"}
    dining = {"category": "dining", "scenario": "daily"}
    travel = {"category": "travel", "scenario": "daily"}
    shopping = {"category": "shopping", "scenario": "daily"}
    with sessions() as db:
        bill = Bill(
            occurred_at=started,
            merchant="Lunch",
            note="",
            amount=-42,
            currency="CNY",
            category="Dining",
            tag_state_json=json.dumps(dining, sort_keys=True),
        )
        db.add(bill)
        db.flush()
        first = _audit(
            bill_id=bill.id,
            created_at=started + timedelta(minutes=1),
            category="Dining",
            state=dining,
            before_category="Unclassified",
            before_state=unclassified,
            superseded=True,
            action="confirm",
            idempotency_key="tag-first",
        )
        db.add(first)
        db.flush()
        db.add(_audit(
            bill_id=bill.id,
            created_at=started + timedelta(minutes=2),
            category="Travel",
            state=travel,
            before_category="Dining",
            before_state=dining,
            tags="Travel,Daily",
            strategy="llm_suggestion",
            confidence=0.8,
            provider="test-model",
            superseded=True,
            action="suggest",
            idempotency_key="tag-suggestion",
        ))
        second = _audit(
            bill_id=bill.id,
            created_at=started + timedelta(minutes=3),
            category="Shopping",
            state=shopping,
            before_category="Dining",
            before_state=dining,
            superseded=True,
            action="confirm",
            undone=True,
            undone_at=started + timedelta(minutes=4),
            idempotency_key="tag-second",
        )
        db.add(second)
        db.flush()
        db.add(_audit(
            bill_id=bill.id,
            created_at=started + timedelta(minutes=4),
            category="Dining",
            state=dining,
            before_category="Shopping",
            before_state=shopping,
            superseded=False,
            action="undo",
            reverses_audit_id=second.id,
            idempotency_key="tag-undo",
            request_payload='{"reason":"restore"}',
        ))
        db.commit()
        return bill.id


def test_tag_shadow_preserves_suggestion_decision_and_exact_reversal(tmp_path):
    _, sessions = _database(tmp_path, "history")
    bill_id = _seed_decision_history(sessions)
    with sessions() as db:
        assert FactShadowMigrationService(db).backfill_and_compare().matched
        source_count = db.query(TagAudit).count()
        report = TagReviewShadowMigrationService(db).backfill_and_compare()
        assert report.matched, report.blockers
        case_id = TAG_CASE_START + bill_id
        case = db.get(ReviewCase, case_id)
        assert (
            case.review_type,
            case.status,
            case.allocation_status,
            case.version,
        ) == ("TAG", "CONFIRMED", "COMPLETE", 4)
        result = json.loads(case.result_json)
        assert result["category"] == "Dining"
        assert result["tag_state"]["category"] == "dining"
        assert result["suggestion"] == {}
        line = db.scalar(select(ReviewCaseBill).where(ReviewCaseBill.case_id == case_id))
        assert (line.bill_id, line.role, line.amount_value) == (bill_id, "TAG", 4200)

        history = db.scalars(
            select(ReviewHistory)
            .where(ReviewHistory.case_id == case_id)
            .order_by(ReviewHistory.version)
        ).all()
        assert [row.operation for row in history] == [
            "CREATE", "UPDATE", "CONFIRM", "RESTORE",
        ]
        assert json.loads(history[1].after_json)["result"]["suggestion"]["provider"] == "test-model"
        assert history[3].reverses_history_id == history[2].id
        assert db.query(TagAudit).count() == source_count

        repeated = TagReviewShadowMigrationService(db).backfill_and_compare()
        assert repeated.matched
        assert repeated.review_case.inserted_count == 0
        assert repeated.review_case_bill.inserted_count == 0
        assert repeated.review_history.inserted_count == 0


def _seed_confirmations(sessions, count: int):
    started = datetime(2026, 9, 11, 9)
    before = {"category": "unclassified"}
    after = {"category": "dining"}
    with sessions() as db:
        for index in range(count):
            bill = Bill(
                occurred_at=started + timedelta(minutes=index),
                merchant=f"merchant-{index}",
                note="",
                amount=-10,
                currency="CNY",
                category="Dining",
                tag_state_json=json.dumps(after, sort_keys=True),
            )
            db.add(bill)
            db.flush()
            db.add(_audit(
                bill_id=bill.id,
                created_at=started,
                category="Dining",
                state=after,
                before_category="Unclassified",
                before_state=before,
                superseded=False,
                action="confirm",
                idempotency_key=f"tag-{index}",
            ))
        db.commit()


def _tag_select_count(tmp_path, count: int) -> int:
    engine, sessions = _database(tmp_path, f"count-{count}")
    _seed_confirmations(sessions, count)
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
            assert TagReviewShadowMigrationService(db).backfill_and_compare().matched
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    return selects


def test_tag_shadow_select_count_does_not_grow_with_rows(tmp_path):
    assert _tag_select_count(tmp_path, 10) == _tag_select_count(tmp_path, 100) == 9
