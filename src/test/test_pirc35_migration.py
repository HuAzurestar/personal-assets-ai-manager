import json
import sqlite3
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.core.target_database import SQL_ASSET_DIR, TARGET_TABLE_NAMES
from backend.mapper.schema_migration_mapper import NEW_TABLES, SchemaMigrationMapper
from backend.parser.statement_parser import parse_statement
from backend.service.schema_migration_service import migrate_copy, verify_ready
from backend.mapper.schema_migration_mapper import fingerprint

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


def test_legacy_gap_is_conserved_and_reported_without_default_repair(tmp_path):
    source,target = tmp_path/'old.db',tmp_path/'new.db'
    legacy_database(source)
    with closing(sqlite3.connect(source)) as c:
        c.execute('UPDATE review_case SET status=1 WHERE id=1')
        c.commit()
    report = migrate_copy(source,target)
    assert report['ready'] and report['coverage_gaps'] == 1
    with closing(sqlite3.connect(target)) as c:
        assert c.execute('SELECT COUNT(id) FROM review_case').fetchone()[0] == 24
        assert c.execute('SELECT status FROM review_case WHERE id=1').fetchone()[0] == 1
        assert c.execute('SELECT COUNT(id) FROM review_transaction_ledger_allocation').fetchone()[0] == 24


@pytest.mark.parametrize('invalid',['2026-02-30T12:00:00.123Z','2026-09-01 00:00:00',
    '2026-09-01T00:00:00+00:00','2026-09-01T00:00:00.1234Z','2026-09-01T25:00:00.000000Z'])
def test_malformed_time_never_padded_into_valid_data(tmp_path,invalid):
    source,target = tmp_path/'old.db',tmp_path/'new.db'
    legacy_database(source)
    with closing(sqlite3.connect(source)) as c:
        c.execute('UPDATE transaction_fact SET created_time=? WHERE id=1',(invalid,))
        c.commit()
    with pytest.raises(ValueError,match='TIMESTAMP_REVIEW_REQUIRED'):
        migrate_copy(source,target)
    assert not target.exists()
    with closing(sqlite3.connect(source)) as c:
        assert c.execute('SELECT created_time FROM transaction_fact WHERE id=1').fetchone()[0] == invalid


def test_registered_time_padding_and_ready_manifest_are_strict(tmp_path):
    source,target = tmp_path/'old.db',tmp_path/'new.db'
    legacy_database(source)
    with closing(sqlite3.connect(source)) as c:
        c.execute("UPDATE review_case SET created_time='2026-09-01T00:00:00.123Z' WHERE id=1")
        c.commit()
        manifest = fingerprint(SchemaMigrationMapper(c).manifest()[1])
    report = migrate_copy(source,target,expected_source_manifest=manifest,candidate_sha='a'*40)
    assert report['candidate_sha'] == 'a'*40 and report['normalized_timestamps'] >= 1
    assert verify_ready(target,report) == dict(ready=True,schema='PIRC-35')
    with closing(sqlite3.connect(target)) as c:
        assert c.execute('SELECT created_time FROM review_case WHERE id=1').fetchone()[0] == '2026-09-01T00:00:00.123000Z'
        c.execute("UPDATE review_case SET title='mock post migration write' WHERE id=1")
        c.commit()
    with pytest.raises(ValueError,match='MIGRATION_MANIFEST_MISMATCH'):
        verify_ready(target,report)
    with closing(sqlite3.connect(target)) as c:
        assert c.execute('SELECT title FROM review_case WHERE id=1').fetchone()[0] == 'mock post migration write'


def test_input_manifest_conflict_drops_only_new_candidate(tmp_path):
    source,target = tmp_path/'old.db',tmp_path/'new.db'
    legacy_database(source)
    with pytest.raises(ValueError,match='MIGRATION_MANIFEST_MISMATCH'):
        migrate_copy(source,target,expected_source_manifest='0'*64)
    assert not target.exists() and source.exists()


@pytest.mark.parametrize('step',range(1,10))
def test_each_rename_add_ddl_failure_leaves_original_and_retries_fresh(tmp_path,monkeypatch,step):
    source,target,retry = (tmp_path/name for name in ('old.db','new.db','retry.db'))
    legacy_database(source)
    original = SchemaMigrationMapper.migrate
    def injected(self,*args):
        count = 0
        def authorize(action,*_):
            nonlocal count
            if action == sqlite3.SQLITE_ALTER_TABLE:
                count += 1
                if count == step:
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.connection.set_authorizer(authorize)
        original(self,*args)
    monkeypatch.setattr(SchemaMigrationMapper,'migrate',injected)
    with pytest.raises(sqlite3.DatabaseError):
        migrate_copy(source,target)
    assert not target.exists()
    with closing(sqlite3.connect(source)) as c:
        assert SchemaMigrationMapper(c).preflight() == set(range(1,25))
    monkeypatch.setattr(SchemaMigrationMapper,'migrate',original)
    assert migrate_copy(source,retry)['ready']


def test_disk_budget_deadline_and_schema_difference_refuse_ready(tmp_path,monkeypatch):
    import backend.service.schema_migration_service as service
    from types import SimpleNamespace
    source,target = tmp_path/'old.db',tmp_path/'new.db'
    legacy_database(source)
    with monkeypatch.context() as patch:
        patch.setattr(service.shutil,'disk_usage',lambda _:SimpleNamespace(free=0))
        with pytest.raises(ValueError,match='MIGRATION_DISK_BUDGET'):
            migrate_copy(source,target)
    assert not target.exists()
    with pytest.raises(ValueError,match='MIGRATION_TIMEOUT'):
        migrate_copy(source,target,seconds=.000001)
    assert not target.exists()
    with closing(sqlite3.connect(source)) as c:
        c.execute('CREATE INDEX unexpected_index ON review_case(title)')
        c.commit()
    with pytest.raises(ValueError,match='SCHEMA_MANIFEST_MISMATCH'):
        migrate_copy(source,target)
    assert not target.exists()


def test_conservation_mismatch_cannot_be_marked_ready(tmp_path,monkeypatch):
    source,target = tmp_path/'old.db',tmp_path/'new.db'
    legacy_database(source)
    original = SchemaMigrationMapper.migrate
    def injected(self,*args):
        original(self,*args)
        self.connection.execute("UPDATE transaction_fact SET summary='fake changed immutable source' WHERE id=1")
    monkeypatch.setattr(SchemaMigrationMapper,'migrate',injected)
    with pytest.raises(ValueError,match='MIGRATION_CONSERVATION_FAILED'):
        migrate_copy(source,target)
    assert not target.exists()


def test_reliable_exact_source_ref_initializes_without_owner_or_source_rewrite(tmp_path):
    source,target = tmp_path/'old.db',tmp_path/'new.db'
    legacy_database(source)
    identity = '990000000000001234'
    with closing(sqlite3.connect(source)) as c:
        amount,direction,occurred = c.execute('SELECT amount,cash_direction,occurred_time FROM transaction_fact WHERE id=1').fetchone()
        normalized = dict(source_type='ccb',currency='CNY',amount_minor=amount if direction==1 else -amount,occurred_at=occurred,
            account=dict(number=identity),source_account=dict(source_namespace='ccb:statement-v1',source_identity=identity,identity_strength='RELIABLE'))
        c.execute('UPDATE transaction_import_file SET source_type=201')
        c.execute('UPDATE transaction_fact SET account_code=? WHERE id=1',(identity,))
        c.execute('UPDATE transaction_import_row SET raw_payload=? WHERE transaction_fact_id=1',(json.dumps(dict(normalized=normalized,raw=dict(mock=True))),))
        c.commit()
    report = migrate_copy(source,target)
    assert report['initialized_refs'] == 1
    with closing(sqlite3.connect(target)) as c:
        assert c.execute('SELECT account_id,source_identity FROM ledger_account_ref').fetchall() == [(0,identity)]
        assert c.execute('SELECT COUNT(*) FROM ledger_account_party').fetchone()[0] == 0
        assert c.execute('SELECT account_ref_id FROM ledger_entry WHERE id=1').fetchone()[0] > 0
        assert c.execute('SELECT account_ref_id FROM ledger_entry WHERE id=2').fetchone()[0] == 0
        assert c.execute('SELECT account_code FROM transaction_fact WHERE id=1').fetchone()[0] == identity


def test_wrong_fact_or_residual_audit_cannot_claim_original_default(tmp_path):
    source,target = tmp_path/'old.db',tmp_path/'new.db'
    legacy_database(source)
    with closing(sqlite3.connect(source)) as c:
        c.execute('UPDATE review_revision SET request_json=? WHERE review_case_id=1',
            (json.dumps(dict(operation='AUTO_REVIEW',fact_id=2)),))
        c.commit()
    with pytest.raises(ValueError,match='DEFAULT_IDENTITY_REQUIRED'):
        migrate_copy(source,target)
    assert not target.exists()


def test_abrupt_ddl_process_exit_requires_new_destination_not_overwrite(tmp_path):
    import os
    import subprocess
    import sys
    source,target,retry = (tmp_path/name for name in ('old.db','interrupted.db','retry.db'))
    legacy_database(source)
    env = {key:value for key,value in os.environ.items() if not key.startswith('PAAM_')}
    env.update(PYTHONPATH=str(Path(__file__).parents[1]),PAAM_DATA_DIR=str(tmp_path),
        PAAM_DATABASE_URL=f'sqlite:///{(tmp_path / "unused.db").as_posix()}')
    script = '''import os,sys
from pathlib import Path
from backend.mapper.schema_migration_mapper import SchemaMigrationMapper
from backend.service.schema_migration_service import migrate_copy
def die(self,*args):
    self.connection.execute("BEGIN IMMEDIATE")
    self.connection.execute("ALTER TABLE review_allocation RENAME TO review_transaction_ledger_allocation")
    os._exit(73)
SchemaMigrationMapper.migrate=die
migrate_copy(Path(sys.argv[1]),Path(sys.argv[2]))
'''
    result = subprocess.run([sys.executable,'-c',script,str(source),str(target)],env=env,capture_output=True,timeout=30)
    assert result.returncode == 73 and target.exists()
    with pytest.raises(ValueError,match='NEW_DESTINATION_REQUIRED'):
        migrate_copy(source,target)
    assert migrate_copy(source,retry)['ready']
    with closing(sqlite3.connect(source)) as c:
        assert SchemaMigrationMapper(c).preflight() == set(range(1,25))


def test_upgraded_copy_starts_real_runtime_and_keeps_cash_sources_and_readiness(tmp_path,monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient
    from backend.core import target_database
    from backend.target_main import app
    source,target = tmp_path/'old.db',tmp_path/'new.db'
    legacy_database(source)
    old_engine = create_engine(f'sqlite:///{source}',connect_args={'check_same_thread':False})
    try:
        with pytest.raises(RuntimeError,match='SCHEMA_MIGRATION_REQUIRED'):
            target_database.init_target_db(bind=old_engine)
    finally:
        old_engine.dispose()
    report = migrate_copy(source,target)
    engine = create_engine(f'sqlite:///{target}',connect_args={'check_same_thread':False})
    sessions = sessionmaker(bind=engine,autoflush=False)
    monkeypatch.setattr(target_database,'engine',engine)
    monkeypatch.setattr(target_database,'SessionLocal',sessions)
    try:
        with TestClient(app) as client:
            assert client.get('/api/health').status_code == 200
            facts = client.get('/paam/ledger/v1/transaction_fact/list?page_size=100').json()['body']
            candidates = client.get('/paam/ledger/v1/candidate/list?page_size=100').json()['body']
            assert facts['total'] == candidates['total'] == 24
            assert all(row['default_review'] and row['coverage']['state']=='FULL' for row in candidates['items'])
            detail = client.get('/paam/ledger/v1/transaction_fact/1').json()['body']
            assert detail['allocations'][0]['transaction_id'] == 1 and len(detail['import_evidence']) == 1
            summary = client.get('/paam/ledger/v1/flow/summary').json()['body']
            assert summary['entry_count'] == 24
            with closing(sqlite3.connect(source)) as c:
                cash = c.execute('SELECT cash_direction,SUM(amount) FROM transaction_fact GROUP BY cash_direction').fetchall()
            totals = summary['totals'][0]
            assert dict(cash)[1] == totals['income_and_expense_in_amount']
            assert dict(cash)[2] == totals['income_and_expense_out_amount']
            assert client.post('/paam/ledger/v1/review',json={}).status_code == 410
        # Lifecycle must not create substitute defaults or rewrite source rows.
        assert verify_ready(target,report)['ready']
    finally:
        engine.dispose()


def test_modified_structural_checks_or_triggers_cannot_match_fresh_schema(tmp_path):
    source,target = tmp_path/'old.db',tmp_path/'new.db'
    legacy_database(source)
    with closing(sqlite3.connect(source)) as c:
        c.execute('CREATE TRIGGER unexpected_write AFTER UPDATE ON review_case BEGIN UPDATE review_case SET title="mock mutation" WHERE id=NEW.id; END')
        c.commit()
    with pytest.raises(ValueError,match='MIGRATION_CONSERVATION_FAILED|SCHEMA_MANIFEST_MISMATCH'):
        migrate_copy(source,target)
    assert not target.exists()


def test_failed_postcommit_ready_probe_preserves_new_write_and_refuses_reuse(tmp_path,monkeypatch):
    import backend.service.schema_migration_service as service
    source,target = tmp_path/'old.db',tmp_path/'new.db'
    legacy_database(source)
    original = service.verify_ready
    def injected(path,report):
        with closing(sqlite3.connect(path)) as c:
            c.execute("UPDATE review_case SET title='mock new write after commit' WHERE id=1")
            c.commit()
        return original(path,report)
    monkeypatch.setattr(service,'verify_ready',injected)
    with pytest.raises(ValueError,match='MIGRATION_MANIFEST_MISMATCH'):
        migrate_copy(source,target)
    assert target.exists()
    with closing(sqlite3.connect(target)) as c:
        assert c.execute('SELECT title FROM review_case WHERE id=1').fetchone()[0] == 'mock new write after commit'
    with pytest.raises(ValueError,match='NEW_DESTINATION_REQUIRED'):
        migrate_copy(source,target)
