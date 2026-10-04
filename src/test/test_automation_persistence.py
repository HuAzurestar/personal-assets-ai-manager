from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from backend.core.target_database import TARGET_TABLE_NAMES, init_target_db
from backend.mapper.auto_tag_rule_mapper import (
    AutoTagRuleMapper,
    decode_method_config,
)
from backend.mapper.setting_mapper import SettingMapper, decode_setting_value
from backend.mapper.tag_assignment_request_mapper import (
    TagAssignmentRequestMapper,
)


SQL_DIR = Path(__file__).parents[1] / "asset" / "sql"
LEGACY_TARGET_TABLES = (
    "transaction_import_file",
    "transaction_import_row",
    "transaction_fact",
    "review_case",
    "review_allocation",
    "review_revision",
    "ledger_entry",
    "tag_view",
    "tag",
    "ledger_entry_tag",
)


def test_additive_init_preserves_existing_rows_and_is_idempotent(tmp_path):
    path = tmp_path / "existing-target.db"
    connection = sqlite3.connect(path)
    try:
        for table_name in LEGACY_TARGET_TABLES:
            connection.executescript(
                (SQL_DIR / f"{table_name}.sql").read_text(encoding="utf-8")
            )
        connection.execute(
            "INSERT INTO tag_view "
            "(id, name, system_name, status, created_time, updated_time) "
            "VALUES (7, 'Existing', 'existing', 'ACTIVE', ?, ?)",
            (
                "2026-09-20T01:02:03.123456Z",
                "2026-09-20T01:02:03.123456Z",
            ),
        )
        connection.commit()
    finally:
        connection.close()

    engine = create_engine(f"sqlite:///{path}")
    try:
        init_target_db(bind=engine)
        init_target_db(bind=engine)
        assert set(inspect(engine).get_table_names()) == set(TARGET_TABLE_NAMES)
        with engine.connect() as database:
            row = database.exec_driver_sql(
                "SELECT id, name, system_name, status, created_time, updated_time "
                "FROM tag_view WHERE id = 7"
            ).one()
        assert row == (
            7,
            "Existing",
            "existing",
            "ACTIVE",
            "2026-09-20T01:02:03.123456Z",
            "2026-09-20T01:02:03.123456Z",
        )
    finally:
        engine.dispose()


def test_automation_sql_contracts_reject_invalid_persisted_values(tmp_path):
    path = tmp_path / "automation-contract.db"
    engine = create_engine(f"sqlite:///{path}")
    try:
        init_target_db(bind=engine)
        database = inspect(engine)
        assert {column["name"] for column in database.get_columns("setting")} == {
            "id", "value_json", "created_time", "updated_time",
        }
        assert {column["name"] for column in database.get_columns("auto_tag_rule")} == {
            "id", "name", "view_id", "method", "method_config_json",
            "last_analysis_json",
            "enabled", "cron", "amount_mode", "rule_revision",
            "scan_after_ledger_id", "scan_epoch", "analyzed_count",
            "failed_count", "suggested_count", "accepted_count",
            "rejected_count", "created_time", "updated_time",
        }
        assert {
            column["name"]
            for column in database.get_columns("tag_assignment_request")
        } == {
            "id", "rule_id", "rule_revision", "ledger_id", "view_id",
            "call_id",
            "proposed_tag_id", "status", "reason_summary", "created_time",
            "updated_time",
        }
        assert database.get_foreign_keys("setting") == []
        assert database.get_foreign_keys("auto_tag_rule") == []
        assert database.get_foreign_keys("tag_assignment_request") == []
        assert {
            index["name"]: tuple(index["column_names"])
            for index in database.get_indexes("auto_tag_rule")
        } == {"ix_auto_tag_rule_view_id": ("view_id",)}

        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "INSERT INTO setting (id, value_json) VALUES (1, ?)",
                ('{"schema_version":1,"automation":{}}',),
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO setting (id, value_json) VALUES (2, '{}')"
                )
            connection.execute(
                "INSERT INTO auto_tag_rule "
                "(name, view_id, method_config_json) VALUES (?, ?, ?)",
                ("Daily", 1, '{"schema_version":1,"model_id":1,"prompt":"x"}'),
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE auto_tag_rule SET analyzed_count = -1 WHERE id = 1"
                )
            connection.execute(
                "INSERT INTO tag_assignment_request "
                "(rule_id, rule_revision, ledger_id, view_id, proposed_tag_id, "
                "reason_summary) VALUES (1, 1, 1, 1, 1, 'synthetic')"
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE tag_assignment_request SET status = 6 WHERE id = 1"
                )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "UPDATE tag_assignment_request SET reason_summary = ? WHERE id = 1",
                    ("x" * 201,),
                )
        finally:
            connection.close()
    finally:
        engine.dispose()


def test_automation_mappers_round_trip_and_survive_restart(tmp_path):
    path = tmp_path / "automation-round-trip.db"
    engine = create_engine(f"sqlite:///{path}")
    now = datetime(2026, 9, 22, 4, 0, 0, 123456, tzinfo=timezone.utc)
    try:
        init_target_db(bind=engine)
        with Session(engine) as db:
            setting_mapper = SettingMapper(db)
            setting_mapper.begin_write()
            setting_mapper.save(
                {"schema_version": 1, "automation": {"models": []}},
                now,
            )
            rule_id = AutoTagRuleMapper(db).create(
                name="Synthetic rule",
                view_id=3,
                method_config={
                    "schema_version": 1,
                    "model_id": 7,
                    "prompt": "synthetic only",
                },
                now=now,
            )
            request_ids = TagAssignmentRequestMapper(db).create_many([
                {
                    "rule_id": rule_id,
                    "rule_revision": 1,
                    "ledger_id": 11,
                    "view_id": 3,
                    "proposed_tag_id": 5,
                    "reason_summary": "synthetic",
                }
            ], now)
            setting_mapper.commit()

        init_target_db(bind=engine)
        with Session(engine) as db:
            setting = SettingMapper(db).get()
            rule = AutoTagRuleMapper(db).get(rule_id)
            request = TagAssignmentRequestMapper(db).get(request_ids[0])
        assert setting is not None
        assert setting["value"] == {
            "schema_version": 1,
            "automation": {"models": []},
        }
        assert rule is not None
        assert rule["method_config"] == {
            "schema_version": 1,
            "model_id": 7,
            "prompt": "synthetic only",
            "prompt_id": "tag-suggestion",
        }
        assert request is not None
        assert request["status"] == 1
        assert request["reason_summary"] == "synthetic"
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("decoder", "payload"),
    [
        (decode_setting_value, "[]"),
        (decode_setting_value, '{"schema_version":2}'),
        (decode_method_config, '{"schema_version":1,"model_id":0,"prompt":"x"}'),
        (decode_method_config, '{"schema_version":1,"model_id":1,"prompt":7}'),
    ],
)
def test_json_contract_decoders_reject_invalid_payloads(decoder, payload):
    with pytest.raises(ValueError):
        decoder(payload)
