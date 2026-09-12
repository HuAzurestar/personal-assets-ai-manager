from __future__ import annotations

from datetime import datetime

from sqlalchemy import create_engine, event, inspect, select, text, update
from sqlalchemy.orm import sessionmaker

import app.database as database
from app.target_database import TargetBase

from app.database import (
    Base,
    Bill,
    ImportArtifact,
    ImportBatch,
    ImportRowIssue,
    LedgerOrigin,
)
from app.models.target import BillFact, BillRaw, ImportFile
from app.services.target_migration_service import FactShadowMigrationService


TARGET_TABLES = (
    "import_file",
    "bill_raw",
    "bill_fact",
    "review_case",
    "review_case_bill",
    "review_history",
    "ledger_entry",
    "ledger_entry_source",
    "tag_view",
    "tag",
    "ledger_entry_tag",
)


def _database(tmp_path, suffix: str = "facts"):
    engine = create_engine(f"sqlite:///{tmp_path / f'target-{suffix}.db'}")
    Base.metadata.create_all(bind=engine)
    TargetBase.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, autoflush=False)


def _seed(sessions, *, extra_bills: int = 0) -> None:
    with sessions() as db:
        batch = ImportBatch(
            source_type="wechat",
            filename="wechat.csv",
            imported_at=datetime(2026, 9, 1, 8),
            row_count=4 + extra_bills,
            imported_count=2 + extra_bills,
            batch_token="upload-group-1",
        )
        db.add(batch)
        db.flush()
        db.add(ImportArtifact(
            import_batch_id=batch.id,
            source_type="wechat",
            filename="wechat.csv",
            file_format="csv",
            archive_entry=None,
            sha256="a" * 64,
        ))

        bills = [
            Bill(
                occurred_at=datetime(2026, 8, 28, 10),
                merchant="coffee",
                note="breakfast",
                amount=-12.34,
                currency="CNY",
                account_name="wechat-wallet",
            ),
            Bill(
                occurred_at=datetime(2026, 8, 30, 18),
                merchant="client",
                note="payment",
                amount=8.0,
                currency="USD",
                account_name="bank-card",
            ),
        ]
        bills.extend(
            Bill(
                occurred_at=datetime(2026, 8, 31, 12),
                merchant=f"merchant-{index}",
                note="",
                amount=-(index + 1.0),
                currency="CNY",
                account_name="wechat-wallet",
            )
            for index in range(extra_bills)
        )
        db.add_all(bills)
        db.flush()

        first_payload = '{"amount":"12.34","merchant":"coffee"}'
        second_payload = '{"amount":"8.00","merchant":"client"}'
        db.add_all([
            LedgerOrigin(
                bill_id=bills[0].id,
                source_type="wechat",
                source_reference="wx-1",
                raw_payload=first_payload,
                import_batch_id=batch.id,
                source_row_number=1,
            ),
            LedgerOrigin(
                bill_id=bills[1].id,
                source_type="wechat",
                source_reference="wx-2",
                raw_payload=second_payload,
                import_batch_id=batch.id,
                source_row_number=2,
            ),
        ])
        for index, bill in enumerate(bills[2:], start=5):
            db.add(LedgerOrigin(
                bill_id=bill.id,
                source_type="wechat",
                source_reference=f"wx-{index}",
                raw_payload=f'{{"row":{index}}}',
                import_batch_id=batch.id,
                source_row_number=index,
            ))
        db.add_all([
            ImportRowIssue(
                import_batch_id=batch.id,
                source_row_number=2,
                raw_payload=second_payload,
                error="currency needed review",
                resolution='{"corrected":true}',
                bill_id=bills[1].id,
                resolved_at=datetime(2026, 9, 2, 9),
            ),
            ImportRowIssue(
                import_batch_id=batch.id,
                source_row_number=3,
                raw_payload='{"status":"cancelled"}',
                error="not posted",
                resolution='{"dismissed":true}',
                bill_id=None,
                resolved_at=datetime(2026, 9, 2, 10),
            ),
            ImportRowIssue(
                import_batch_id=batch.id,
                source_row_number=4,
                raw_payload='{"amount":"?"}',
                error="invalid amount",
                resolution="",
                bill_id=None,
                resolved_at=None,
            ),
        ])
        db.commit()


def test_target_schema_uses_implicit_ids_without_sql_foreign_keys(tmp_path):
    engine, _ = _database(tmp_path, "schema")
    database = inspect(engine)
    assert set(TARGET_TABLES).issubset(database.get_table_names())
    assert all(not database.get_foreign_keys(table) for table in TARGET_TABLES)


def test_target_schema_adds_account_columns_to_existing_shadow_tables(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'target-upgrade.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE bill_fact (id INTEGER PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE ledger_entry (id INTEGER PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE review_case_bill (id INTEGER PRIMARY KEY)"))
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "DATABASE_URL", "sqlite:///target-upgrade.db")

    database.ensure_target_schema()

    schema = inspect(engine)
    assert "account_code" in {
        item["name"] for item in schema.get_columns("bill_fact")
    }
    assert {"in_account_code", "out_account_code"}.issubset({
        item["name"] for item in schema.get_columns("ledger_entry")
    })
    assert "party" in {
        item["name"] for item in schema.get_columns("review_case_bill")
    }


def test_fact_shadow_backfill_is_lossless_idempotent_and_does_not_touch_legacy(tmp_path):
    engine, sessions = _database(tmp_path)
    _seed(sessions)
    with sessions() as db:
        legacy_before = (
            db.query(Bill).count(),
            db.query(LedgerOrigin).count(),
            db.query(ImportRowIssue).count(),
        )
        report = FactShadowMigrationService(db).backfill_and_compare()

    assert report.matched is True
    assert report.bill_fact.inserted_count == 2
    assert report.bill_raw.inserted_count == 4
    assert report.import_file.inserted_count == 1
    with sessions() as db:
        facts = db.scalars(select(BillFact).order_by(BillFact.id)).all()
        assert [(fact.cash_direction, fact.amount_value, fact.amount_scale, fact.currency_code) for fact in facts] == [
            ("OUT", 1234, 2, "CNY"),
            ("IN", 800, 2, "USD"),
        ]
        assert [fact.account_code for fact in facts] == ["wechat-wallet", "bank-card"]
        import_file = db.scalar(select(ImportFile))
        assert (
            import_file.total_count,
            import_file.success_count,
            import_file.skip_count,
            import_file.issue_count,
            import_file.status,
        ) == (4, 2, 1, 1, "PARTIAL")
        assert import_file.period_start == "2026-08-28T10:00:00"
        assert import_file.period_end == "2026-08-30T18:00:00"
        raws = db.scalars(select(BillRaw).order_by(BillRaw.source_row_number)).all()
        assert [raw.parse_status for raw in raws] == ["SUCCESS", "SUCCESS", "SKIPPED", "INVALID"]
        assert raws[0].raw_payload == '{"amount":"12.34","merchant":"coffee"}'
        assert (
            db.query(Bill).count(),
            db.query(LedgerOrigin).count(),
            db.query(ImportRowIssue).count(),
        ) == legacy_before

        second = FactShadowMigrationService(db).backfill_and_compare()
        assert second.matched is True
        assert second.bill_fact.inserted_count == 0
        assert second.bill_raw.inserted_count == 0
        assert second.import_file.inserted_count == 0


def test_fact_shadow_reports_drift_without_overwriting_target(tmp_path):
    _, sessions = _database(tmp_path, "drift")
    _seed(sessions)
    with sessions() as db:
        initial = FactShadowMigrationService(db).backfill_and_compare()
        assert initial.matched
        first_id = db.scalar(select(BillFact.id).order_by(BillFact.id))
        db.execute(update(BillFact).where(BillFact.id == first_id).values(summary="tampered"))
        db.commit()

        report = FactShadowMigrationService(db).backfill_and_compare()
        assert report.matched is False
        assert report.bill_fact.mismatched_ids == [first_id]
        assert db.scalar(select(BillFact.summary).where(BillFact.id == first_id)) == "tampered"


def _migration_select_count(tmp_path, bill_count: int) -> int:
    engine, sessions = _database(tmp_path, f"count-{bill_count}")
    _seed(sessions, extra_bills=bill_count - 2)
    selects = 0

    def count_selects(_connection, _cursor, statement, _parameters, _context, _many):
        nonlocal selects
        if statement.lstrip().upper().startswith("SELECT"):
            selects += 1

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        with sessions() as db:
            report = FactShadowMigrationService(db).backfill_and_compare()
            assert report.matched
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)
    return selects


def test_fact_shadow_backfill_select_count_does_not_grow_with_rows(tmp_path):
    assert _migration_select_count(tmp_path, 10) == _migration_select_count(tmp_path, 100) == 11
