import json
import sqlite3
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.core.target_database import SQL_ASSET_DIR, TARGET_TABLE_NAMES
from backend.mapper.schema_migration_mapper import NEW_TABLES, SchemaMigrationMapper
from backend.parser.statement_parser import parse_statement
from backend.service.schema_migration_service import migrate_copy

FIXTURE = Path(__file__).parent / "fixtures/pirc35"


def legacy_database(path, sample="ccb-2.csv"):
    with closing(sqlite3.connect(path)) as c:
        for name in TARGET_TABLE_NAMES:
            if name in NEW_TABLES:
                continue
            asset = FIXTURE / "legacy-allocation.sql" if name == "review_transaction_ledger_allocation" else FIXTURE / "legacy-ledger.sql" if name == "ledger_entry" else SQL_ASSET_DIR / f"{name}.sql"
            sql = asset.read_text(encoding="utf-8")
            c.executescript(sql)
        document = parse_statement((FIXTURE / sample).read_bytes(), sample, source_timezone=ZoneInfo("Asia/Hong_Kong"))
        c.execute("INSERT INTO transaction_import_file(id,filename,sha256,total_count,success_count,status) VALUES(1,?,'mock-sha256',24,24,1)", (sample,))
        values = []
        for i, row in enumerate(document["rows"], 1):
            values.append((i, f"mock-{i}", row["occurred_at"], 1 if row["amount_minor"] > 0 else 2,
                           abs(row["amount_minor"]), "CNY", "MOCK"))
        c.executemany("INSERT INTO transaction_fact(id,fact_key,occurred_time,cash_direction,amount,currency_code,account_code) VALUES(?,?,?,?,?,?,?)", values)
        c.executemany("INSERT INTO review_case(id,behavior_type,status,title) VALUES(?,0,0,'mock')", [(v[0],) for v in values])
        c.executemany("INSERT INTO ledger_entry(id,entry_type,entry_direction,amount,currency_code,account_code,occurred_time) VALUES(?,0,?,?,?,'MOCK',?)",
                      [(v[0], v[3], v[4], v[5], v[2]) for v in values])
        c.executemany("INSERT INTO review_allocation(id,review_case_id,transaction_fact_id,ledger_entry_id,amount,currency_code) VALUES(?,?,?,?,?,?)",
                      [(v[0], v[0], v[0], v[0], v[4], v[5]) for v in values])
        c.executemany("INSERT INTO review_revision(review_case_id,operation,actor,request_json) VALUES(?,0,'system',?)",
                      [(v[0], json.dumps({"operation": "AUTO_REVIEW", "fact_id": v[0]})) for v in values])
        c.executemany("INSERT INTO transaction_import_row(transaction_fact_id,transaction_import_file_id,source_row_number,raw_payload,row_status) VALUES(?,1,?,?,1)",
                      [(i, i, json.dumps(row["raw"], ensure_ascii=False)) for i, row in enumerate(document["rows"], 1)])
        c.execute("INSERT INTO tag_view(id,name,system_name) VALUES(1,'mock view','mock')")
        c.execute("INSERT INTO tag(id,view_id,name,system_name) VALUES(1,1,'Unclassified','unclassified')")
        c.executemany("INSERT INTO ledger_entry_tag(ledger_id,tag_id) VALUES(?,1)", [(v[0],) for v in values])
        c.commit()


def columns(c, table):
    return {row[1]: (row[2], row[3], row[4].replace(", ", ",") if row[4] else row[4], row[5])
            for row in c.execute(f'PRAGMA table_info("{table}")')}


def indexes(c, table):
    return {(row[2], tuple(value[2] for value in c.execute(f'PRAGMA index_info("{row[1]}")')), row[4])
            for row in c.execute(f'PRAGMA index_list("{table}")')}


@pytest.mark.parametrize("sample", [entry["file"] for entry in json.loads((FIXTURE / "manifest.json").read_text(encoding="utf-8"))["fixtures"]])
def test_copy_preserves_mock_database_and_matches_fresh_schema(tmp_path, sample):
    source, target, fresh = (tmp_path / name for name in ("old.db", "new.db", "fresh.db"))
    legacy_database(source, sample)
    with closing(sqlite3.connect(source)) as old:
        _, before = SchemaMigrationMapper(old).manifest()
    assert migrate_copy(source, target)["verified_defaults"] == 24
    with closing(sqlite3.connect(source)) as old, closing(sqlite3.connect(target)) as new, closing(sqlite3.connect(fresh)) as empty:
        assert SchemaMigrationMapper(old).manifest()[1] == before
        assert "review_allocation" in {v[0] for v in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in TARGET_TABLE_NAMES:
            empty.executescript((SQL_ASSET_DIR / f"{table}.sql").read_text(encoding="utf-8"))
            assert columns(new, table) == columns(empty, table)
            assert indexes(new, table) == indexes(empty, table)
        plan = new.execute("EXPLAIN QUERY PLAN SELECT review_id FROM review_transaction_ledger_allocation WHERE transaction_id=?", (1,)).fetchall()
        assert any("INDEX" in row[3] and "transaction_id" in row[3] for row in plan)
        assert new.execute("SELECT COUNT(id) FROM position").fetchone()[0] == 0
        assert new.execute("SELECT COUNT(id) FROM review_ledger_position_leg_allocation").fetchone()[0] == 0


def test_old_manual_zero_is_not_a_system_default(tmp_path):
    source, target = tmp_path / "old.db", tmp_path / "new.db"
    legacy_database(source)
    with closing(sqlite3.connect(source)) as c:
        c.execute("INSERT INTO review_case(id,behavior_type,status,title) VALUES(100,0,1,'manual')")
        c.commit()
    migrate_copy(source, target)
    with closing(sqlite3.connect(target)) as c:
        assert c.execute("SELECT behavior_type FROM review_case WHERE id=100").fetchone()[0] == 4
        assert c.execute("SELECT behavior_type FROM review_case WHERE id=1").fetchone()[0] == 0


def test_legal_multi_review_legacy_split_is_preserved(tmp_path):
    source, target = tmp_path / "old.db", tmp_path / "new.db"
    legacy_database(source)
    with closing(sqlite3.connect(source)) as c:
        c.execute("UPDATE review_case SET status=1 WHERE id=1")
        c.executemany("INSERT INTO review_case(id,behavior_type,status,title) VALUES(?,0,0,'split')", [(100,), (101,)])
        occurred = c.execute("SELECT occurred_time FROM transaction_fact WHERE id=1").fetchone()[0]
        c.executemany("INSERT INTO ledger_entry(id,entry_type,entry_direction,amount,currency_code,account_code,occurred_time) VALUES(?,0,1,?,'CNY','MOCK',?)",
                      [(100, 600, occurred), (101, 425, occurred)])
        c.executemany("INSERT INTO review_allocation(review_case_id,transaction_fact_id,ledger_entry_id,amount,currency_code) VALUES(?,1,?,?,'CNY')",
                      [(100, 100, 600), (101, 101, 425)])
        c.commit()
    migrate_copy(source, target)
    with closing(sqlite3.connect(target)) as c:
        assert c.execute("SELECT cash_amount FROM review_transaction_ledger_allocation WHERE transaction_id=1 ORDER BY id").fetchall() == [(1025,), (600,), (425,)]
        assert c.execute("SELECT behavior_type FROM review_case WHERE id>=100 ORDER BY id").fetchall() == [(4,), (4,)]


def test_broken_default_aborts_without_changing_source(tmp_path):
    source, target = tmp_path / "old.db", tmp_path / "new.db"
    legacy_database(source)
    with closing(sqlite3.connect(source)) as c:
        c.execute("DELETE FROM review_revision WHERE review_case_id=1")
        c.commit()
    with pytest.raises(ValueError, match="DEFAULT_IDENTITY_REQUIRED"):
        migrate_copy(source, target)
    assert not target.exists()
    with closing(sqlite3.connect(source)) as c:
        assert c.execute("SELECT COUNT(id) FROM review_allocation").fetchone()[0] == 24


def test_migration_failure_rolls_back_new_copy(tmp_path, monkeypatch):
    source, target = tmp_path / "old.db", tmp_path / "new.db"
    legacy_database(source)
    migrate = SchemaMigrationMapper.migrate
    def fail(self, *args):
        migrate(self, *args)
        raise RuntimeError("INJECTED_FAILURE")
    monkeypatch.setattr(SchemaMigrationMapper, "migrate", fail)
    with pytest.raises(RuntimeError, match="INJECTED_FAILURE"):
        migrate_copy(source, target)
    assert not target.exists()
    with closing(sqlite3.connect(source)) as c:
        assert c.execute("SELECT COUNT(id) FROM review_allocation").fetchone()[0] == 24


def test_copy_never_overwrites_an_existing_destination(tmp_path):
    source = tmp_path / "old.db"
    legacy_database(source)
    with pytest.raises(ValueError, match="NEW_DESTINATION_REQUIRED"):
        migrate_copy(source, source)


def test_backup_includes_committed_wal_and_original_is_restorable(tmp_path):
    source, target, restored = (tmp_path / name for name in ("old.db", "new.db", "restored.db"))
    legacy_database(source)
    with closing(sqlite3.connect(source)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE review_case SET title='mock WAL evidence' WHERE id=1")
        writer.commit()
        migrate_copy(source, target)
        with closing(sqlite3.connect(target)) as upgraded:
            assert upgraded.execute("SELECT title FROM review_case WHERE id=1").fetchone()[0] == "mock WAL evidence"
        with closing(sqlite3.connect(restored)) as recovery:
            writer.backup(recovery)
            assert recovery.execute("SELECT COUNT(id) FROM review_allocation").fetchone()[0] == 24
            assert SchemaMigrationMapper(recovery).preflight() == set(range(1, 25))
