from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event, select, update
from sqlalchemy.orm import sessionmaker

from app.database import Base, Bill, ReviewMatter, ReviewMatterRevision
from app.target_database import TargetBase
from app.models.target import ReviewCase, ReviewCaseBill, ReviewHistory
from app.services.review_migration_service import (
    MATTER_CASE_START,
    ReviewMatterShadowMigrationService,
)
from app.services.target_migration_service import FactShadowMigrationService


def _database(tmp_path, suffix: str):
    engine = create_engine(f"sqlite:///{tmp_path / f'review-shadow-{suffix}.db'}")
    Base.metadata.create_all(bind=engine)
    TargetBase.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False)


def _snapshot(bill_id: int, *, own_expense: int, receivable: int) -> str:
    return json.dumps({
        "title": "Dinner AA",
        "scenarios": ["AA", "shared meal"],
        "lines": [
            {
                "bill_id": bill_id,
                "amount_cents": own_expense,
                "role": "expense",
                "party": "",
            },
            {
                "bill_id": bill_id,
                "amount_cents": receivable,
                "role": "receivable",
                "party": "Alice",
            },
        ],
        "own_accounts_confirmed": False,
        "balances": [
            {"kind": "receivable", "party": "Alice", "amount_cents": receivable},
        ],
    }, ensure_ascii=False, sort_keys=True)


def _seed_one_updated_matter(sessions) -> tuple[int, int]:
    with sessions() as db:
        bill = Bill(
            occurred_at=datetime(2026, 9, 1, 18),
            merchant="Restaurant",
            note="Dinner",
            amount=-100,
            currency="CNY",
        )
        db.add(bill)
        db.flush()
        matter = ReviewMatter(version=2, created_at=datetime(2026, 9, 2, 9))
        db.add(matter)
        db.flush()
        db.add_all([
            ReviewMatterRevision(
                matter_id=matter.id,
                version=1,
                action="confirm",
                snapshot=_snapshot(bill.id, own_expense=5000, receivable=5000),
                reason="Initial review",
                actor="local-user",
                idempotency_key="matter-v1",
                request_payload='{"expected_version":0}',
                created_at=datetime(2026, 9, 2, 9),
            ),
            ReviewMatterRevision(
                matter_id=matter.id,
                version=2,
                action="confirm",
                snapshot=_snapshot(bill.id, own_expense=3000, receivable=7000),
                reason="Correct split",
                actor="local-user",
                idempotency_key="matter-v2",
                request_payload='{"expected_version":1}',
                created_at=datetime(2026, 9, 2, 10),
            ),
        ])
        db.commit()
        return matter.id, bill.id


def test_manual_matter_shadow_backfill_preserves_current_lines_and_history(tmp_path):
    _, sessions = _database(tmp_path, "matter")
    matter_id, bill_id = _seed_one_updated_matter(sessions)
    with sessions() as db:
        fact_report = FactShadowMigrationService(db).backfill_and_compare()
        assert fact_report.matched
        legacy_counts = (
            db.query(ReviewMatter).count(),
            db.query(ReviewMatterRevision).count(),
        )
        report = ReviewMatterShadowMigrationService(db).backfill_and_compare()
        assert report.matched
        assert report.review_case.inserted_count == 1
        assert report.review_case_bill.inserted_count == 2
        assert report.review_history.inserted_count == 2

        case_id = MATTER_CASE_START + matter_id
        case = db.get(ReviewCase, case_id)
        assert (
            case.review_type,
            case.status,
            case.allocation_status,
            case.version,
            case.title,
        ) == ("AA", "CONFIRMED", "COMPLETE", 2, "Dinner AA")
        assert "bill_id" not in case.result_json
        lines = db.scalars(
            select(ReviewCaseBill)
            .where(ReviewCaseBill.case_id == case_id)
            .order_by(ReviewCaseBill.id)
        ).all()
        assert [(line.bill_id, line.role, line.party, line.amount_value) for line in lines] == [
            (bill_id, "EXPENSE", "", 3000),
            (bill_id, "AA_PAID", "Alice", 7000),
        ]
        history = db.scalars(
            select(ReviewHistory)
            .where(ReviewHistory.case_id == case_id)
            .order_by(ReviewHistory.version)
        ).all()
        assert [row.operation for row in history] == ["CREATE", "UPDATE"]
        assert history[1].before_json == history[0].after_json
        assert history[1].snapshot_hash == hashlib.sha256(
            history[1].after_json.encode("utf-8")
        ).hexdigest()
        assert json.loads(history[1].after_json)["lines"][1]["party"] == "Alice"
        assert (
            db.query(ReviewMatter).count(),
            db.query(ReviewMatterRevision).count(),
        ) == legacy_counts

        repeated = ReviewMatterShadowMigrationService(db).backfill_and_compare()
        assert repeated.matched
        assert repeated.review_case.inserted_count == 0
        assert repeated.review_case_bill.inserted_count == 0
        assert repeated.review_history.inserted_count == 0


def test_manual_matter_shadow_reports_drift_without_overwrite(tmp_path):
    _, sessions = _database(tmp_path, "drift")
    matter_id, _ = _seed_one_updated_matter(sessions)
    with sessions() as db:
        assert FactShadowMigrationService(db).backfill_and_compare().matched
        assert ReviewMatterShadowMigrationService(db).backfill_and_compare().matched
        case_id = MATTER_CASE_START + matter_id
        db.execute(update(ReviewCase).where(ReviewCase.id == case_id).values(title="tampered"))
        db.commit()

        report = ReviewMatterShadowMigrationService(db).backfill_and_compare()
        assert report.matched is False
        assert report.review_case.mismatched_ids == [case_id]
        assert db.get(ReviewCase, case_id).title == "tampered"


def _seed_matter_count(sessions, count: int) -> None:
    with sessions() as db:
        started = datetime(2026, 9, 1, 8)
        for index in range(count):
            bill = Bill(
                occurred_at=started + timedelta(minutes=index),
                merchant=f"merchant-{index}",
                note="",
                amount=-100,
                currency="CNY",
            )
            db.add(bill)
            db.flush()
            matter = ReviewMatter(version=1, created_at=started)
            db.add(matter)
            db.flush()
            db.add(ReviewMatterRevision(
                matter_id=matter.id,
                version=1,
                action="confirm",
                snapshot=_snapshot(bill.id, own_expense=4000, receivable=6000),
                reason="Reviewed",
                actor="local-user",
                idempotency_key=f"matter-{index}",
                request_payload='{"expected_version":0}',
                created_at=started,
            ))
        db.commit()


def _review_select_count(tmp_path, count: int) -> int:
    engine, sessions = _database(tmp_path, f"count-{count}")
    _seed_matter_count(sessions, count)
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
            assert ReviewMatterShadowMigrationService(db).backfill_and_compare().matched
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    return selects


def test_manual_matter_shadow_select_count_does_not_grow_with_rows(tmp_path):
    assert _review_select_count(tmp_path, 10) == _review_select_count(tmp_path, 100) == 9
