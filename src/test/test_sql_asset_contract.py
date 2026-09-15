from __future__ import annotations

import sqlite3
from pathlib import Path


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
            "amount_value",
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
            "projection_version",
        } & ledger_columns

        connection.execute(
            """
            INSERT INTO ledger_entry (
                entry_type, entry_direction, amount_value, currency_code,
                account_code, occurred_time
            ) VALUES (0, 2, 500000, 'CNY', 'cash', '2026-09-15T12:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO review_allocation (
                review_case_id, transaction_fact_id, ledger_entry_id,
                amount_value, currency_code
            ) VALUES (1, 1, 1, 500000, 'CNY')
            """
        )
        try:
            connection.execute(
                """
                INSERT INTO review_allocation (
                    review_case_id, transaction_fact_id, ledger_entry_id,
                    amount_value, currency_code
                ) VALUES (1, 2, 1, 500000, 'CNY')
                """
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("one ledger entry accepted more than one fact")
    finally:
        connection.close()
