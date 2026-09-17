from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from backend.entity import (
    CASH_DIRECTION_OUT,
    IMPORT_FILE_FORMAT_CSV,
    IMPORT_FILE_STATUS_IMPORTED,
    IMPORT_ROW_STATUS_ACCEPTED,
    IMPORT_SOURCE_CCB_BANK,
    TransactionFact,
    TransactionImportFile,
    TransactionImportRow,
)
from backend.mapper.target_economic_mapper import TargetEconomicMapper


SQL_DIR = Path(__file__).parents[1] / "asset" / "sql"
EXPECTED_TABLES = {
    "ledger_entry",
    "ledger_entry_tag",
    "review_allocation",
    "review_case",
    "review_revision",
    "tag",
    "tag_view",
    "transaction_fact",
    "transaction_import_file",
    "transaction_import_row",
}


def _create_target_schema() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    for path in sorted(SQL_DIR.glob("*.sql")):
        connection.executescript(path.read_text(encoding="utf-8"))
    return connection


def test_target_sql_assets_create_the_reviewed_tables():
    connection = _create_target_schema()
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'table'"
            )
        }
        assert tables == EXPECTED_TABLES
        assert connection.execute("PRAGMA encoding").fetchone()[0] == "UTF-8"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()


def test_every_target_table_has_common_columns_and_inline_comments():
    connection = _create_target_schema()
    try:
        for table in EXPECTED_TABLES:
            columns = {
                row[1]: row
                for row in connection.execute(f'PRAGMA table_info("{table}")')
            }
            assert {"id", "created_time", "updated_time"} <= columns.keys()
            assert columns["id"][5] == 1
            assert columns["created_time"][3] == 1
            assert columns["updated_time"][3] == 1
            assert columns["created_time"][4] is not None
            assert columns["updated_time"][4] is not None

            create_sql = connection.execute(
                "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()[0]
            assert "/*" in create_sql
    finally:
        connection.close()


def test_ledger_entry_is_confirmed_single_fact_cash_projection():
    connection = _create_target_schema()
    try:
        ledger_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(ledger_entry)")
        }
        assert {
            "entry_type",
            "entry_direction",
            "amount",
            "amount_scale",
            "currency_code",
            "account_code",
            "counterparty_account_ref",
            "occurred_time",
        } <= ledger_columns
        assert not {
            "status",
            "title",
            "start_time",
            "end_time",
            "claim_key",
            "claim_side",
            "reversal_of_id",
            "input_hash",
        } & ledger_columns

        connection.execute(
            """
            INSERT INTO ledger_entry (
                entry_type, entry_direction, amount, currency_code,
                account_code, occurred_time
            ) VALUES (0, 2, 500000, 'CNY', 'cash', '2026-09-15T12:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO review_allocation (
                review_case_id, transaction_fact_id, ledger_entry_id,
                amount, currency_code
            ) VALUES (1, 1, 1, 500000, 'CNY')
            """
        )
        try:
            connection.execute(
                """
                INSERT INTO review_allocation (
                    review_case_id, transaction_fact_id, ledger_entry_id,
                    amount, currency_code
                ) VALUES (1, 2, 1, 500000, 'CNY')
                """
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("one ledger entry accepted more than one fact")
    finally:
        connection.close()


def test_review_case_is_a_minimal_published_lifecycle():
    connection = _create_target_schema()
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(review_case)")
        }
        assert columns == {
            "id",
            "behavior_type",
            "status",
            "title",
            "created_time",
            "updated_time",
        }

        connection.execute("INSERT INTO review_case DEFAULT VALUES")
        row = connection.execute(
            "SELECT behavior_type, status, title FROM review_case"
        ).fetchone()
        assert row == (0, 0, "")

        for statement in (
            "INSERT INTO review_case (behavior_type) VALUES (2)",
            "INSERT INTO review_case (status) VALUES (2)",
        ):
            try:
                connection.execute(statement)
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError(f"review_case accepted invalid value: {statement}")
    finally:
        connection.close()


def test_review_allocation_only_stores_published_relationships():
    connection = _create_target_schema()
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(review_allocation)")
        }
        assert columns == {
            "id",
            "review_case_id",
            "transaction_fact_id",
            "ledger_entry_id",
            "amount",
            "amount_scale",
            "currency_code",
            "created_time",
            "updated_time",
        }

        for column in (
            "review_case_id",
            "transaction_fact_id",
            "ledger_entry_id",
            "amount",
        ):
            try:
                connection.execute(
                    f"""
                    INSERT INTO review_allocation (
                        review_case_id, transaction_fact_id, ledger_entry_id,
                        amount, currency_code
                    ) VALUES (
                        {0 if column == 'review_case_id' else 1},
                        {0 if column == 'transaction_fact_id' else 1},
                        {0 if column == 'ledger_entry_id' else 1},
                        {0 if column == 'amount' else 1},
                        'CNY'
                    )
                    """
                )
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError(f"review_allocation accepted invalid {column}")
    finally:
        connection.close()


def test_review_revision_is_append_only_audit_without_business_version():
    connection = _create_target_schema()
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(review_revision)")
        }
        assert columns == {
            "id",
            "review_case_id",
            "operation",
            "request_json",
            "before_json",
            "after_json",
            "actor",
            "reason",
            "idempotency_key",
            "created_time",
            "updated_time",
        }

        connection.execute(
            """
            INSERT INTO review_revision (
                review_case_id, operation, request_json, after_json,
                idempotency_key
            ) VALUES (1, 0, '{"behavior_type":0}', '{"status":0}', 'create:1')
            """
        )
        for statement in (
            "INSERT INTO review_revision (review_case_id) VALUES (0)",
            "INSERT INTO review_revision (review_case_id, operation) VALUES (1, 4)",
            "INSERT INTO review_revision (review_case_id, request_json) VALUES (1, 'bad json')",
            "INSERT INTO review_revision (review_case_id, idempotency_key) VALUES (1, 'create:1')",
        ):
            try:
                connection.execute(statement)
            except sqlite3.IntegrityError:
                pass
            else:
                raise AssertionError(f"review_revision accepted invalid row: {statement}")
    finally:
        connection.close()


def test_transaction_fact_sql_asset_matches_entity_and_mapper(tmp_path):
    database_path = tmp_path / "transaction-fact.db"
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            (SQL_DIR / "transaction_fact.sql").read_text(encoding="utf-8")
        )
    finally:
        connection.close()

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with Session(engine) as db:
            fact = TransactionFact(
                fact_key="sql-asset-transaction-fact",
                occurred_time=datetime(2026, 9, 16, 12, 30),
                cash_direction=CASH_DIRECTION_OUT,
                amount=500000,
                amount_scale=2,
                currency_code="CNY",
                account_code="cash",
                counterparty_name="测试对手方",
                counterparty_account_ref="counterparty-account",
                summary="SQL asset contract",
            )
            db.add(fact)
            db.commit()

            stored_direction = db.scalar(select(TransactionFact.cash_direction))
            assert stored_direction == CASH_DIRECTION_OUT
            mapped = TargetEconomicMapper(db).facts([fact.id])[0]
            assert mapped.cash_direction == "OUT"
            assert mapped.counterparty == "测试对手方"
            fact_id = fact.id
        with engine.connect() as connection:
            stored = connection.execute(text(
                "SELECT cash_direction, occurred_time, created_time "
                "FROM transaction_fact WHERE id = :fact_id"
            ), {"fact_id": fact_id}).mappings().one()
            assert stored["cash_direction"] == CASH_DIRECTION_OUT
            assert stored["occurred_time"] == "2026-09-16T12:30:00.000Z"
            assert "T" in stored["created_time"]
            assert stored["created_time"].endswith("Z")
    finally:
        engine.dispose()


def test_transaction_fact_entity_has_only_current_physical_columns():
    assert set(TransactionFact.__table__.columns.keys()) == {
        "id",
        "fact_key",
        "occurred_time",
        "cash_direction",
        "amount",
        "amount_scale",
        "currency_code",
        "account_code",
        "counterparty_name",
        "counterparty_account_ref",
        "summary",
        "created_time",
        "updated_time",
    }


def test_transaction_import_file_sql_asset_matches_entity(tmp_path):
    database_path = tmp_path / "transaction-import-file.db"
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            (SQL_DIR / "transaction_import_file.sql").read_text(encoding="utf-8")
        )
    finally:
        connection.close()

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with Session(engine) as db:
            import_file = TransactionImportFile(
                batch_code="batch-1",
                source_type=IMPORT_SOURCE_CCB_BANK,
                filename="ccb.zip",
                file_format=IMPORT_FILE_FORMAT_CSV,
                sha256="a" * 64,
                period_start="2026-09-01T00:00:00.000Z",
                period_end="2026-09-15T23:59:59.999Z",
                total_count=2,
                success_count=2,
                skip_count=0,
                issue_count=0,
                status=IMPORT_FILE_STATUS_IMPORTED,
            )
            db.add(import_file)
            db.commit()
            import_file_id = import_file.id

        with engine.connect() as connection:
            stored = connection.execute(text(
                "SELECT source_type, file_format, status, filename "
                "FROM transaction_import_file WHERE id = :import_file_id"
            ), {"import_file_id": import_file_id}).mappings().one()
            assert dict(stored) == {
                "source_type": IMPORT_SOURCE_CCB_BANK,
                "file_format": IMPORT_FILE_FORMAT_CSV,
                "status": IMPORT_FILE_STATUS_IMPORTED,
                "filename": "ccb.zip",
            }
    finally:
        engine.dispose()


def test_transaction_import_file_entity_has_only_current_physical_columns():
    assert set(TransactionImportFile.__table__.columns.keys()) == {
        "id",
        "batch_code",
        "source_type",
        "filename",
        "file_format",
        "sha256",
        "period_start",
        "period_end",
        "total_count",
        "success_count",
        "skip_count",
        "issue_count",
        "status",
        "created_time",
        "updated_time",
    }


def test_transaction_import_row_sql_asset_matches_entity(tmp_path):
    database_path = tmp_path / "transaction-import-row.db"
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            (SQL_DIR / "transaction_import_row.sql").read_text(encoding="utf-8")
        )
    finally:
        connection.close()

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with Session(engine) as db:
            import_row = TransactionImportRow(
                transaction_fact_id=7,
                transaction_import_file_id=3,
                source_row_number=1,
                source_reference="reference-1",
                raw_payload='{"raw":{"amount":"10.00"}}',
                raw_hash="b" * 64,
                row_status=IMPORT_ROW_STATUS_ACCEPTED,
            )
            db.add(import_row)
            db.commit()
            import_row_id = import_row.id

        with engine.connect() as connection:
            stored = connection.execute(text(
                "SELECT transaction_fact_id, transaction_import_file_id, "
                "row_status, raw_payload FROM transaction_import_row "
                "WHERE id = :import_row_id"
            ), {"import_row_id": import_row_id}).mappings().one()
            assert dict(stored) == {
                "transaction_fact_id": 7,
                "transaction_import_file_id": 3,
                "row_status": IMPORT_ROW_STATUS_ACCEPTED,
                "raw_payload": '{"raw":{"amount":"10.00"}}',
            }
    finally:
        engine.dispose()


def test_transaction_import_row_entity_has_only_current_physical_columns():
    assert set(TransactionImportRow.__table__.columns.keys()) == {
        "id",
        "transaction_fact_id",
        "transaction_import_file_id",
        "source_row_number",
        "source_reference",
        "raw_payload",
        "raw_hash",
        "row_status",
        "issue_code",
        "issue_message",
        "created_time",
        "updated_time",
    }
