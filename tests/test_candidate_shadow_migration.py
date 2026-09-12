from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.database import Base, Bill, CandidateActionLog, ReviewCandidate
from app.models.target import ReviewCase, ReviewCaseBill, ReviewHistory
from app.services.candidate_migration_service import (
    CANDIDATE_CASE_START,
    CandidateReviewShadowMigrationService,
)
from app.services.target_migration_service import FactShadowMigrationService


def _database(tmp_path, suffix: str):
    engine = create_engine(f"sqlite:///{tmp_path / f'candidate-shadow-{suffix}.db'}")
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False)


def _state(*, status: str, retained_bill_id=None, resolved_at=None, bills=None):
    return json.dumps({
        "candidate": {
            "status": status,
            "transfer_group_id": None,
            "transfer_kind": None,
            "retained_bill_id": retained_bill_id,
            "resolved_at": resolved_at.isoformat() if resolved_at else None,
        },
        "bills": bills or {},
    }, ensure_ascii=False)


def _seed_confirmed_then_undone_duplicate(sessions):
    started = datetime(2026, 9, 3, 9)
    confirmed_at = datetime(2026, 9, 3, 10)
    undone_at = datetime(2026, 9, 3, 11)
    with sessions() as db:
        bills = [
            Bill(
                occurred_at=started + timedelta(minutes=index),
                merchant="same order",
                note="",
                amount=-10,
                currency="CNY",
            )
            for index in range(2)
        ]
        db.add_all(bills)
        db.flush()
        candidate = ReviewCandidate(
            candidate_type="duplicate",
            bill_id=bills[0].id,
            related_bill_id=bills[1].id,
            member_bill_ids=json.dumps([bill.id for bill in bills]),
            group_fingerprint=f"duplicate:{bills[0].id}:{bills[1].id}",
            confidence=.99,
            reason="same provider reference",
            status="pending",
            transfer_group_id=None,
            transfer_kind=None,
            retained_bill_id=None,
            resolved_at=None,
            created_at=started,
        )
        db.add(candidate)
        db.flush()
        initial_bill_state = {
            str(bill.id): {
                "aggregate_excluded": False,
                "transfer_group_id": None,
                "duplicate_of_id": None,
            }
            for bill in bills
        }
        confirmed_bill_state = {
            str(bills[0].id): {
                "aggregate_excluded": False,
                "transfer_group_id": None,
                "duplicate_of_id": None,
            },
            str(bills[1].id): {
                "aggregate_excluded": True,
                "transfer_group_id": None,
                "duplicate_of_id": bills[0].id,
            },
        }
        before = _state(status="pending", bills=initial_bill_state)
        confirmed = _state(
            status="duplicate_excluded",
            retained_bill_id=bills[0].id,
            resolved_at=confirmed_at,
            bills=confirmed_bill_state,
        )
        confirm_action = CandidateActionLog(
            candidate_id=candidate.id,
            action="resolve_duplicate",
            before_state=before,
            after_state=confirmed,
            actor="local-user",
            reason="confirmed duplicate",
            reverses_action_id=None,
            idempotency_key="duplicate-confirm",
            request_payload='{"action":"resolve_duplicate"}',
            created_at=confirmed_at,
            undone=True,
            undone_at=undone_at,
        )
        db.add(confirm_action)
        db.flush()
        db.add(CandidateActionLog(
            candidate_id=candidate.id,
            action="undo",
            before_state=confirmed,
            after_state=before,
            actor="local-user",
            reason="wrong pair",
            reverses_action_id=confirm_action.id,
            idempotency_key="duplicate-undo",
            request_payload='{"reason":"wrong pair"}',
            created_at=undone_at,
            undone=False,
            undone_at=None,
        ))
        db.commit()
        return candidate.id, [bill.id for bill in bills]


def test_candidate_shadow_preserves_pending_boundary_and_reversible_history(tmp_path):
    _, sessions = _database(tmp_path, "undo")
    candidate_id, bill_ids = _seed_confirmed_then_undone_duplicate(sessions)
    with sessions() as db:
        assert FactShadowMigrationService(db).backfill_and_compare().matched
        legacy_counts = (
            db.query(ReviewCandidate).count(),
            db.query(CandidateActionLog).count(),
        )
        report = CandidateReviewShadowMigrationService(db).backfill_and_compare()
        assert report.matched
        case_id = CANDIDATE_CASE_START + candidate_id
        case = db.get(ReviewCase, case_id)
        assert (case.review_type, case.status, case.allocation_status, case.version) == (
            "DUPLICATE", "PENDING", "PARTIAL", 3,
        )
        assert "member_bill_ids" not in case.result_json
        lines = db.scalars(
            select(ReviewCaseBill)
            .where(ReviewCaseBill.case_id == case_id)
            .order_by(ReviewCaseBill.id)
        ).all()
        assert [(line.bill_id, line.role, line.amount_value) for line in lines] == [
            (bill_ids[0], "DUPLICATE_MEMBER", 1000),
            (bill_ids[1], "DUPLICATE_MEMBER", 1000),
        ]
        history = db.scalars(
            select(ReviewHistory)
            .where(ReviewHistory.case_id == case_id)
            .order_by(ReviewHistory.version)
        ).all()
        assert [row.operation for row in history] == ["CREATE", "CONFIRM", "RESTORE"]
        assert history[2].reverses_history_id == history[1].id
        assert history[1].after_json == history[2].before_json
        assert json.loads(history[1].after_json)["lines"][0]["role"] == "DUPLICATE_RETAINED"
        assert (
            db.query(ReviewCandidate).count(),
            db.query(CandidateActionLog).count(),
        ) == legacy_counts

        repeated = CandidateReviewShadowMigrationService(db).backfill_and_compare()
        assert repeated.matched
        assert repeated.review_case.inserted_count == 0
        assert repeated.review_case_bill.inserted_count == 0
        assert repeated.review_history.inserted_count == 0


def test_confirmed_transfer_candidate_becomes_balanced_confirmed_case(tmp_path):
    _, sessions = _database(tmp_path, "transfer")
    started = datetime(2026, 9, 5, 9)
    confirmed_at = datetime(2026, 9, 5, 10)
    with sessions() as db:
        outbound = Bill(
            occurred_at=started,
            merchant="Bank transfer out",
            note="",
            amount=-100,
            currency="CNY",
        )
        inbound = Bill(
            occurred_at=started + timedelta(minutes=1),
            merchant="Wallet transfer in",
            note="",
            amount=100,
            currency="CNY",
        )
        db.add_all([outbound, inbound])
        db.flush()
        candidate = ReviewCandidate(
            candidate_type="transfer",
            bill_id=outbound.id,
            related_bill_id=inbound.id,
            member_bill_ids=json.dumps([outbound.id, inbound.id]),
            group_fingerprint="",
            confidence=.95,
            reason="equal opposite amounts",
            status="personal_transfer_grouped",
            transfer_group_id="transfer-1",
            transfer_kind="personal",
            retained_bill_id=None,
            resolved_at=confirmed_at,
            created_at=started,
        )
        db.add(candidate)
        db.flush()
        before = _state(status="pending")
        after = json.dumps({
            "candidate": {
                "status": "personal_transfer_grouped",
                "transfer_group_id": "transfer-1",
                "transfer_kind": "personal",
                "retained_bill_id": None,
                "resolved_at": confirmed_at.isoformat(),
            },
            "bills": {},
        })
        db.add(CandidateActionLog(
            candidate_id=candidate.id,
            action="confirm_personal_transfer",
            before_state=before,
            after_state=after,
            actor="local-user",
            reason="accounts verified",
            reverses_action_id=None,
            idempotency_key="transfer-confirm",
            request_payload='{"action":"confirm_personal_transfer"}',
            created_at=confirmed_at,
            undone=False,
            undone_at=None,
        ))
        db.commit()

        assert FactShadowMigrationService(db).backfill_and_compare().matched
        report = CandidateReviewShadowMigrationService(db).backfill_and_compare()
        assert report.matched
        case_id = CANDIDATE_CASE_START + candidate.id
        case = db.get(ReviewCase, case_id)
        assert (case.review_type, case.status, case.allocation_status) == (
            "TRANSFER", "CONFIRMED", "COMPLETE",
        )
        lines = db.scalars(
            select(ReviewCaseBill)
            .where(ReviewCaseBill.case_id == case_id)
            .order_by(ReviewCaseBill.id)
        ).all()
        assert [(line.role, line.amount_value) for line in lines] == [
            ("TRANSFER_OUT", 10000),
            ("TRANSFER_IN", 10000),
        ]


def _seed_pending_candidates(sessions, count: int):
    started = datetime(2026, 9, 4, 9)
    with sessions() as db:
        for index in range(count):
            first = Bill(
                occurred_at=started + timedelta(minutes=index * 2),
                merchant=f"duplicate-{index}",
                note="",
                amount=-10,
                currency="CNY",
            )
            second = Bill(
                occurred_at=started + timedelta(minutes=index * 2 + 1),
                merchant=f"duplicate-{index}",
                note="",
                amount=-10,
                currency="CNY",
            )
            db.add_all([first, second])
            db.flush()
            db.add(ReviewCandidate(
                candidate_type="duplicate",
                bill_id=first.id,
                related_bill_id=second.id,
                member_bill_ids=json.dumps([first.id, second.id]),
                group_fingerprint=f"duplicate:{first.id}:{second.id}",
                confidence=.9,
                reason="possible duplicate",
                status="pending",
                created_at=started,
            ))
        db.commit()


def _candidate_select_count(tmp_path, count: int) -> int:
    engine, sessions = _database(tmp_path, f"count-{count}")
    _seed_pending_candidates(sessions, count)
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
            assert CandidateReviewShadowMigrationService(db).backfill_and_compare().matched
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    return selects


def test_candidate_shadow_select_count_does_not_grow_with_rows(tmp_path):
    assert _candidate_select_count(tmp_path, 10) == _candidate_select_count(tmp_path, 100) == 9
