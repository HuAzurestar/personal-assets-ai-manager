"""R21: measured default ordering and index-only upgrades of fictional copies."""
from contextlib import closing
import hashlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text

from backend.core import target_database
from backend.entity import TransactionFact
from backend.mapper.schema_migration_mapper import (
    SchemaMigrationMapper, expected_schema, schema_profile,
)
from backend.service.schema_migration_service import migrate_copy, verify_ready
from backend.target_main import app
from test_pirc35_aggregate import seed_contributions
from test_pirc35_migration import legacy_database


MIXED_INDEX = "ix_transaction_fact_time_desc_id_asc"
ASC_INDEX = "ix_transaction_fact_occurred_time_id"


def index_keys(connection, name):
    return [(row[2], row[3]) for row in connection.execute(
        f'PRAGMA index_xinfo("{name}")') if row[5]]


def distribute_fixture():
    # Test setup only. Both sides of each valid cash chain have the same time.
    with target_database.engine.begin() as connection:
        connection.execute(text("UPDATE transaction_fact SET occurred_time="
            "strftime('%Y-%m-%dT00:00:00.000000Z','2022-01-01','+'||(id % 730)||' days')"))
        connection.execute(text("UPDATE ledger_entry SET occurred_time=(SELECT occurred_time "
            "FROM transaction_fact WHERE transaction_fact.id=ledger_entry.id)"))


def captured_request(client, path, params):
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if "default_review_id" in statement and "LIMIT" in statement and "UNION ALL" not in statement:
            statements.append((statement, parameters))

    event.listen(target_database.engine, "before_cursor_execute", capture)
    try:
        response = client.get("/paam/ledger/v1/candidate/" + path, params=params)
    finally:
        event.remove(target_database.engine, "before_cursor_execute", capture)
    assert response.status_code == 200, response.text
    assert len(statements) == 1
    return response.json()["body"], statements[0]


@pytest.mark.parametrize("distributed", [False, True])
@pytest.mark.parametrize("path", ["list", "search"])
@pytest.mark.parametrize("size", [20, 100])
def test_actual_default_page_uses_mixed_index_without_tie_group_sort(distributed, path, size):
    with TestClient(app) as client:
        seed_contributions(1000)
        if distributed:
            distribute_fixture()
        params = dict(page_size=size)
        if path == "search":
            params["query"] = '[{"key":"summary","word":"Mock contribution"}]'
        body, (statement, parameters) = captured_request(client, path, params)
        with target_database.engine.connect() as connection:
            expected = connection.exec_driver_sql("SELECT id FROM transaction_fact "
                "ORDER BY occurred_time DESC,id ASC LIMIT ?", (size,)).scalars().all()
            plan = connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, parameters).all()
        assert [row["transaction_id"] for row in body["items"]] == expected
        assert all(row["coverage"]["state"] == "FULL" and row["cash_amount"] == 1
            and row["default_review"]["review_id"] == row["transaction_id"] for row in body["items"])
        assert any(MIXED_INDEX in row[3] for row in plan), plan
        # count(DISTINCT) may still use a temporary structure: only the
        # avoidable mixed-direction ordering sort is forbidden here.
        assert not any("TEMP B-TREE" in row[3] and "ORDER BY" in row[3] for row in plan), plan


@pytest.mark.parametrize("distributed", [False, True])
@pytest.mark.parametrize("sorter", [None, [{"key":"occurred_time","direction":"asc"}],
    [{"key":"id","direction":"desc"}]])
def test_default_and_alternate_order_keep_deep_cursor_and_empty_batches(distributed, sorter):
    with TestClient(app) as client:
        seed_contributions(1000)
        if distributed:
            distribute_fixture()
        params = dict(page_size=20, query='[{"key":"summary","word":"never-present"}]',
            filter='{"op":"AND","expression":[{"key":"coverage_state","op":"=","val":"FULL"},'
                '{"key":"account_ref_id","op":"=","val":0}]}')
        order = "occurred_time DESC,id ASC" if sorter is None else (
            "occurred_time ASC,id ASC" if sorter[0]["key"] == "occurred_time" else "id DESC")
        if sorter is not None:
            params["sorter"] = json.dumps(sorter)
        first = client.get("/paam/ledger/v1/candidate/search", params=params)
        assert first.status_code == 200, first.text
        body = first.json()["body"]
        assert body["items"] == [] and body["scanned_count"] == 20 and body["has_more"]
        cursor = json.loads(body["next_cursor"])
        with target_database.engine.connect() as connection:
            ordered = connection.exec_driver_sql("SELECT id,occurred_time FROM transaction_fact ORDER BY " + order).all()
        # Valid synthetic deep boundary, not a claim of 45 HTTP batches.
        boundary = ordered[899]
        cursor.update(last_id=boundary[0], sort_values=([boundary[1],boundary[0]]
            if sorter is None or sorter[0]["key"] == "occurred_time" else [boundary[0]]))
        deep = client.get("/paam/ledger/v1/candidate/search", params=params | dict(cursor=json.dumps(cursor)))
        assert deep.status_code == 200, deep.text
        result = deep.json()["body"]
        assert result["items"] == [] and result["scanned_count"] == 20 and result["has_more"]
        assert json.loads(result["next_cursor"])["last_id"] == ordered[919][0]
        changed = client.get("/paam/ledger/v1/candidate/search", params=params | dict(
            page_size=21, cursor=json.dumps(cursor)))
        assert changed.status_code == 422 and changed.json()["body"]["code"] == "LIST_CURSOR_INVALID"


def test_asset_and_entity_index_directions_match(tmp_path):
    target_database.ensure_target_schema()
    with closing(target_database.engine.raw_connection()) as connection:
        assert index_keys(connection, MIXED_INDEX) == [("occurred_time",1),("id",0)]
        assert index_keys(connection, ASC_INDEX) == [("occurred_time",0),("id",0)]
    engine = create_engine("sqlite:///" + str(tmp_path / "entity-only.db"))
    try:
        TransactionFact.__table__.create(engine)
        with closing(engine.raw_connection()) as connection:
            assert index_keys(connection, MIXED_INDEX) == [("occurred_time",1),("id",0)]
            assert index_keys(connection, ASC_INDEX) == [("occurred_time",0),("id",0)]
    finally:
        engine.dispose()


def test_existing_twenty_table_copy_gains_only_index_and_is_idempotent():
    seed_contributions(30)
    with closing(target_database.engine.raw_connection()) as connection:
        # Reconstruct a previous 20-table schema in this test's disposable DB.
        connection.execute(f'DROP INDEX IF EXISTS "{MIXED_INDEX}"')
        connection.commit()
        before = SchemaMigrationMapper(connection).manifest()[1]
        assert index_keys(connection, MIXED_INDEX) == []
    for _ in range(2):
        target_database.ensure_target_schema()
        with closing(target_database.engine.raw_connection()) as connection:
            assert index_keys(connection, MIXED_INDEX) == [("occurred_time",1),("id",0)]
            assert SchemaMigrationMapper(connection).manifest()[1] == before
            assert schema_profile(connection) == expected_schema(
                target_database.SQL_ASSET_DIR, target_database.TARGET_TABLE_NAMES)


def test_legacy_fourteen_table_copy_gets_directional_index_without_source_changes(tmp_path):
    source, target = tmp_path / "old.db", tmp_path / "candidate.db"
    legacy_database(source)
    with closing(sqlite3.connect(source)) as connection:
        connection.execute(f'DROP INDEX IF EXISTS "{MIXED_INDEX}"')
        connection.commit()
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    report = migrate_copy(source, target)
    assert report["verified_defaults"] == 24
    assert verify_ready(target, report) == dict(ready=True, schema="PIRC-35")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    with closing(sqlite3.connect(target)) as connection:
        assert index_keys(connection, MIXED_INDEX) == [("occurred_time",1),("id",0)]
        assert schema_profile(connection) == expected_schema(
            target_database.SQL_ASSET_DIR, target_database.TARGET_TABLE_NAMES)


def test_fifty_thousand_ties_do_not_evaluate_all_sparse_outputs_to_sort_twenty_rows(record_property):
    with TestClient(app) as client:
        seed_contributions(50000)
        _, (statement, parameters) = captured_request(client, "list", dict(page_size=20))
        with closing(target_database.engine.raw_connection()) as connection:
            assert index_keys(connection, MIXED_INDEX) == [("occurred_time",1),("id",0)]

            def vm_work(mode):
                ticks = []
                connection.set_progress_handler(lambda: ticks.append(1) or 0, 1000)
                try:
                    rows = connection.execute(statement + " /* " + mode + " */", parameters).fetchall()
                finally:
                    connection.set_progress_handler(None, 0)
                return rows, len(ticks)

            optimized, bounded_work = vm_work("mixed-index")
            connection.execute(f'DROP INDEX "{MIXED_INDEX}"')
            connection.commit()
            control, old_work = vm_work("original-index-control")
            assert optimized == control and len(optimized) == 20
            record_property("mixed_index_vm_ticks_1000", bounded_work)
            record_property("original_index_vm_ticks_1000", old_work)
            # Deterministic SQL work, not a fragile wall-clock SLA assertion.
            assert bounded_work * 2 < old_work, (bounded_work, old_work)
        target_database.ensure_target_schema()
