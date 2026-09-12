from sqlalchemy import create_engine, inspect, text

from app.target_database import TARGET_TABLE_NAMES, ensure_target_schema


def test_target_schema_is_exact_and_uses_only_implicit_foreign_keys(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'target-schema.db'}")
    ensure_target_schema(bind=engine)

    schema = inspect(engine)
    assert set(schema.get_table_names()) == set(TARGET_TABLE_NAMES)
    for table_name in TARGET_TABLE_NAMES:
        assert schema.get_foreign_keys(table_name) == []
        columns = {column["name"] for column in schema.get_columns(table_name)}
        assert {"id", "created_time", "updated_time"}.issubset(columns)


def test_target_schema_upgrade_adds_columns_and_hot_indexes(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'target-upgrade.db'}")
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE bill_fact (id INTEGER PRIMARY KEY, occurred_time DATETIME)"
        ))
        connection.execute(text(
            "CREATE TABLE bill_raw ("
            "id INTEGER PRIMARY KEY, bill_id INTEGER, source_reference VARCHAR(160))"
        ))
        connection.execute(text(
            "CREATE TABLE ledger_entry (id INTEGER PRIMARY KEY, start_time DATETIME)"
        ))
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
    assert {"in_account_code", "out_account_code"}.issubset({
        item["name"] for item in schema.get_columns("ledger_entry")
    })
    assert "party" in {
        item["name"] for item in schema.get_columns("review_case_bill")
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
        },
        "ledger_entry_source": {
            "ix_ledger_entry_source_ledger_kind_id",
        },
    }
    for table_name, index_names in expected.items():
        actual = {item["name"] for item in schema.get_indexes(table_name)}
        assert index_names.issubset(actual)
