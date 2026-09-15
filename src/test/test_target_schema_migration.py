from sqlalchemy import create_engine, inspect, text

from backend.core.target_database import TARGET_TABLE_NAMES, ensure_target_schema


def test_target_schema_is_exact_and_uses_only_implicit_foreign_keys(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'target-schema.db'}")
    ensure_target_schema(bind=engine)

    schema = inspect(engine)
    assert set(schema.get_table_names()) == set(TARGET_TABLE_NAMES)
    for table_name in TARGET_TABLE_NAMES:
        assert schema.get_foreign_keys(table_name) == []
        columns = {column["name"] for column in schema.get_columns(table_name)}
        assert {"id", "created_time", "updated_time"}.issubset(columns)


def test_target_schema_upgrade_contracts_ledger_and_drops_sources(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'target-upgrade.db'}")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE bill_fact (id INTEGER PRIMARY KEY, occurred_time DATETIME)"
        ))
        connection.execute(text(
            "CREATE TABLE bill_raw ("
            "id INTEGER PRIMARY KEY, bill_id INTEGER, source_reference VARCHAR(160))"
        ))
        connection.execute(text("""
            CREATE TABLE ledger_entry (
                id INTEGER PRIMARY KEY,
                economic_type TEXT NOT NULL DEFAULT 'TRANSACTION',
                cash_direction TEXT NOT NULL DEFAULT 'UNKNOWN',
                start_time TEXT NOT NULL,
                in_amount_value INTEGER NOT NULL DEFAULT 0,
                out_amount_value INTEGER NOT NULL DEFAULT 0,
                in_currency_code TEXT NOT NULL DEFAULT 'CNY',
                out_currency_code TEXT NOT NULL DEFAULT 'CNY',
                in_account_code TEXT NOT NULL DEFAULT 'UNKNOWN',
                out_account_code TEXT NOT NULL DEFAULT 'UNKNOWN'
            )
        """))
        connection.execute(text("""
            INSERT INTO ledger_entry (
                id, economic_type, cash_direction, start_time,
                in_amount_value, out_amount_value,
                in_currency_code, out_currency_code,
                in_account_code, out_account_code
            ) VALUES (
                7, 'TRANSACTION', 'OUT', '2026-09-15T10:20:30Z',
                0, 12345, 'CNY', 'USD', 'unused', 'wallet'
            )
        """))
        connection.execute(text(
            "CREATE TABLE ledger_entry_source ("
            "id INTEGER PRIMARY KEY, ledger_id INTEGER, "
            "source_kind VARCHAR(20), source_id INTEGER)"
        ))
        connection.execute(text(
            "CREATE TABLE review_case_bill ("
            "id INTEGER PRIMARY KEY, case_id INTEGER, bill_id INTEGER)"
        ))

    ensure_target_schema(bind=engine)

    schema = inspect(engine)
    assert "account_code" in {
        item["name"] for item in schema.get_columns("bill_fact")
    }
    assert "party" in {
        item["name"] for item in schema.get_columns("review_case_bill")
    }
    assert "economic_id" in {
        item["name"] for item in schema.get_columns("review_case_bill")
    }
    assert "entry_type" in {
        item["name"] for item in schema.get_columns("review_case_bill")
    }
    assert "behavior_code" in {
        item["name"] for item in schema.get_columns("review_case")
    }
    assert {
        "id",
        "entry_type",
        "entry_direction",
        "amount_value",
        "amount_scale",
        "currency_code",
        "account_code",
        "counterparty_account_ref",
        "occurred_time",
        "created_time",
        "updated_time",
    } == {
        item["name"] for item in schema.get_columns("ledger_entry")
    }
    assert "ledger_entry_source" not in schema.get_table_names()
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT id, entry_type, entry_direction, amount_value, amount_scale, "
            "currency_code, account_code, counterparty_account_ref, occurred_time "
            "FROM ledger_entry"
        )).mappings().one()
    assert dict(row) == {
        "id": 7,
        "entry_type": 0,
        "entry_direction": 2,
        "amount_value": 12345,
        "amount_scale": 2,
        "currency_code": "USD",
        "account_code": "wallet",
        "counterparty_account_ref": "",
        "occurred_time": "2026-09-15T10:20:30Z",
    }
    expected = {
        "bill_raw": {
            "ix_bill_raw_bill_id_id",
            "ix_bill_raw_source_reference_bill_id",
        },
        "bill_fact": {"ix_bill_fact_occurred_time_id"},
        "review_case_bill": {
            "ix_review_case_bill_bill_case",
            "ix_review_case_bill_case_id",
            "ix_review_case_bill_economic_id",
        },
        "ledger_entry": {"ix_ledger_entry_occurred_time_id"},
    }
    for table_name, index_names in expected.items():
        actual = {item["name"] for item in schema.get_indexes(table_name)}
        assert index_names.issubset(actual)
