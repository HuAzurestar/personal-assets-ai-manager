from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.database import (
    Base,
    Bill,
    ImportArtifact,
    ImportBatch,
    ImportIssueAction,
    ImportRowIssue,
    LedgerOrigin,
)
from app.models.target import ReviewCase, ReviewCaseBill, ReviewHistory
from app.services.import_issue_migration_service import (
    IMPORT_ISSUE_CASE_START,
    ImportIssueReviewShadowMigrationService,
)
from app.services.target_migration_service import FactShadowMigrationService


def _database(tmp_path, suffix: str):
    engine = create_engine(f"sqlite:///{tmp_path / f'import-issue-shadow-{suffix}.db'}")
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False)


def _seed_resolved_and_reopened(sessions):
    started = datetime(2026, 9, 12, 9)
    resolved_payload = json.dumps({
        "actor": "local-user",
        "reason": "confirmed amount",
        "corrected_fields": {"amount": 8, "currency": "USD"},
    }, sort_keys=True)
    dismissed_payload = json.dumps({
        "actor": "local-user",
        "action": "not_a_posted_cny_transaction",
        "reason": "not posted",
    }, sort_keys=True)
    reopened_payload = json.dumps({"reason": "check again"}, sort_keys=True)
    with sessions() as db:
        batch = ImportBatch(
            source_type="wechat",
            filename="wechat.csv",
            imported_at=started,
            row_count=2,
            imported_count=1,
            batch_token="batch-1",
        )
        db.add(batch)
        db.flush()
        db.add(ImportArtifact(
            import_batch_id=batch.id,
            source_type="wechat",
            filename="wechat.csv",
            file_format="csv",
            archive_entry=None,
            sha256="b" * 64,
        ))
        bill = Bill(
            occurred_at=started - timedelta(days=1),
            merchant="client",
            note="",
            amount=8,
            currency="USD",
        )
        db.add(bill)
        db.flush()
        resolved_raw = '{"amount":"8","currency":"USD"}'
        db.add(LedgerOrigin(
            bill_id=bill.id,
            source_type="wechat",
            source_reference="wx-1",
            raw_payload=resolved_raw,
            import_batch_id=batch.id,
            source_row_number=1,
        ))
        resolved = ImportRowIssue(
            import_batch_id=batch.id,
            source_row_number=1,
            raw_payload=resolved_raw,
            error="currency required confirmation",
            resolution=resolved_payload,
            bill_id=bill.id,
            resolved_at=started + timedelta(minutes=1),
        )
        reopened = ImportRowIssue(
            import_batch_id=batch.id,
            source_row_number=2,
            raw_payload='{"amount":"?"}',
            error="amount is invalid",
            resolution=dismissed_payload,
            bill_id=None,
            resolved_at=None,
        )
        db.add_all([resolved, reopened])
        db.flush()
        db.add_all([
            ImportIssueAction(
                issue_id=resolved.id,
                action="resolve",
                payload=resolved_payload,
                actor="local-user",
                created_at=started + timedelta(minutes=1),
            ),
            ImportIssueAction(
                issue_id=reopened.id,
                action="dismiss",
                payload=dismissed_payload,
                actor="local-user",
                created_at=started + timedelta(minutes=2),
            ),
            ImportIssueAction(
                issue_id=reopened.id,
                action="reopen",
                payload=reopened_payload,
                actor="local-user",
                created_at=started + timedelta(minutes=3),
            ),
        ])
        db.commit()
        return resolved.id, reopened.id, bill.id


def test_import_issue_shadow_separates_raw_problem_from_review_history(tmp_path):
    _, sessions = _database(tmp_path, "history")
    resolved_id, reopened_id, bill_id = _seed_resolved_and_reopened(sessions)
    with sessions() as db:
        assert FactShadowMigrationService(db).backfill_and_compare().matched
        source_counts = (
            db.query(ImportRowIssue).count(),
            db.query(ImportIssueAction).count(),
        )
        report = ImportIssueReviewShadowMigrationService(db).backfill_and_compare()
        assert report.matched, report.blockers

        resolved_case_id = IMPORT_ISSUE_CASE_START + resolved_id
        resolved_case = db.get(ReviewCase, resolved_case_id)
        assert (
            resolved_case.review_type,
            resolved_case.status,
            resolved_case.allocation_status,
            resolved_case.version,
        ) == ("FACT_CONFLICT", "CONFIRMED", "COMPLETE", 2)
        resolved_line = db.scalar(
            select(ReviewCaseBill).where(ReviewCaseBill.case_id == resolved_case_id)
        )
        assert (
            resolved_line.bill_id,
            resolved_line.role,
            resolved_line.amount_value,
            resolved_line.currency_code,
        ) == (bill_id, "FACT_ACCEPTED", 800, "USD")

        reopened_case_id = IMPORT_ISSUE_CASE_START + reopened_id
        reopened_case = db.get(ReviewCase, reopened_case_id)
        assert (
            reopened_case.status,
            reopened_case.allocation_status,
            reopened_case.version,
        ) == ("PENDING", "CONFLICT", 3)
        assert db.scalar(
            select(ReviewCaseBill.id).where(ReviewCaseBill.case_id == reopened_case_id)
        ) is None
        reopened_history = db.scalars(
            select(ReviewHistory)
            .where(ReviewHistory.case_id == reopened_case_id)
            .order_by(ReviewHistory.version)
        ).all()
        assert [row.operation for row in reopened_history] == [
            "CREATE", "CONFIRM", "RESTORE",
        ]
        assert reopened_history[2].reverses_history_id == reopened_history[1].id
        assert json.loads(reopened_case.result_json)["bill_raw_id"] > 0
        assert (
            db.query(ImportRowIssue).count(),
            db.query(ImportIssueAction).count(),
        ) == source_counts

        repeated = ImportIssueReviewShadowMigrationService(db).backfill_and_compare()
        assert repeated.matched
        assert repeated.review_case.inserted_count == 0
        assert repeated.review_case_bill.inserted_count == 0
        assert repeated.review_history.inserted_count == 0


def _seed_resolved(sessions, count: int):
    started = datetime(2026, 9, 12, 10)
    payload = json.dumps({
        "actor": "local-user",
        "reason": "confirmed",
        "corrected_fields": {"amount": 10, "currency": "CNY"},
    }, sort_keys=True)
    with sessions() as db:
        batch = ImportBatch(
            source_type="wechat",
            filename="batch.csv",
            imported_at=started,
            row_count=count,
            imported_count=count,
            batch_token="batch-count",
        )
        db.add(batch)
        db.flush()
        db.add(ImportArtifact(
            import_batch_id=batch.id,
            source_type="wechat",
            filename="batch.csv",
            file_format="csv",
            archive_entry=None,
            sha256="c" * 64,
        ))
        for index in range(count):
            bill = Bill(
                occurred_at=started + timedelta(minutes=index),
                merchant=f"merchant-{index}",
                note="",
                amount=-10,
                currency="CNY",
            )
            db.add(bill)
            db.flush()
            raw = json.dumps({"row": index}, sort_keys=True)
            db.add(LedgerOrigin(
                bill_id=bill.id,
                source_type="wechat",
                source_reference=f"wx-{index}",
                raw_payload=raw,
                import_batch_id=batch.id,
                source_row_number=index + 1,
            ))
            issue = ImportRowIssue(
                import_batch_id=batch.id,
                source_row_number=index + 1,
                raw_payload=raw,
                error="amount required confirmation",
                resolution=payload,
                bill_id=bill.id,
                resolved_at=started,
            )
            db.add(issue)
            db.flush()
            db.add(ImportIssueAction(
                issue_id=issue.id,
                action="resolve",
                payload=payload,
                actor="local-user",
                created_at=started,
            ))
        db.commit()


def _import_issue_select_count(tmp_path, count: int) -> int:
    engine, sessions = _database(tmp_path, f"count-{count}")
    _seed_resolved(sessions, count)
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
            assert ImportIssueReviewShadowMigrationService(db).backfill_and_compare().matched
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    return selects


def test_import_issue_shadow_select_count_does_not_grow_with_rows(tmp_path):
    assert _import_issue_select_count(tmp_path, 10) == _import_issue_select_count(tmp_path, 100) == 10
