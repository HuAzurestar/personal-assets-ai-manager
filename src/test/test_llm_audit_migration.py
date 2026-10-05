import sqlite3
import json
from pathlib import Path
import pytest
from backend.core.llm_audit_migration import LEGACY_COLUMNS, migrate_llm_audit

ASSET = Path(__file__).parents[1] / "asset/sql/llm_prompt_audit.sql"


def legacy():
    db = sqlite3.connect(":memory:")
    ints = {"id", "rule_id", "rule_revision", "ledger_id", "model_id", "attempt", "response_truncated"}
    db.execute("CREATE TABLE llm_prompt_audit (" + ",".join(f'{name} {"INTEGER" if name in ints else "TEXT"} NOT NULL' for name in LEGACY_COLUMNS) + ")")
    values = tuple(1 if name in ints else "INSUFFICIENT" if name == "status" else
                   '{"messages":[{"role":"user","content":"fictional正文"}]}' if name == "request_json" else
                   "旧返回正文" if name == "response_text" else "2026-01-01T00:00:00.000000Z" if name.endswith("time") else "fixture"
                   for name in LEGACY_COLUMNS)
    db.execute("INSERT INTO llm_prompt_audit VALUES (" + ",".join("?" for _ in values) + ")", values)
    db.commit()
    return db, values


def test_migration_preserves_every_legacy_column_and_is_idempotent():
    db, expected = legacy()
    migrate_llm_audit(db, ASSET)
    migrate_llm_audit(db, ASSET)
    assert db.execute("SELECT " + ",".join(LEGACY_COLUMNS) + " FROM llm_prompt_audit").fetchone() == expected
    assert db.execute("SELECT input_tokens, output_tokens, total_tokens, source FROM llm_prompt_audit").fetchone() == (None, None, None, "legacy.tag-scan")
    assert [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")] == ["llm_prompt_audit"]
    db.close()


def test_migration_failure_rolls_back_table_rebuild(tmp_path):
    db, expected = legacy()
    invalid = tmp_path / "invalid.sql"
    invalid.write_text(ASSET.read_text(encoding="utf8") + "INVALID SQL;\n", encoding="utf8")
    with pytest.raises(sqlite3.Error):
        migrate_llm_audit(db, invalid)
    assert db.execute("SELECT " + ",".join(LEGACY_COLUMNS) + " FROM llm_prompt_audit").fetchone() == expected
    assert "metadata_json" not in {r[1] for r in db.execute("PRAGMA table_info(llm_prompt_audit)")}
    db.close()


def test_unknown_schema_is_refused_without_modification():
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE llm_prompt_audit(id INTEGER)")
    db.commit()
    with pytest.raises(RuntimeError):
        migrate_llm_audit(db, ASSET)
    assert [r[1] for r in db.execute("PRAGMA table_info(llm_prompt_audit)")] == ["id"]
    db.close()


@pytest.mark.parametrize("status,state", [
    ("INSUFFICIENT", "RESPONSE_RECEIVED"), ("SUGGESTED", "RESPONSE_RECEIVED"),
    ("REJECTED", "RESPONSE_RECEIVED"), ("STARTED", "MAY_HAVE_EXECUTED"),
    ("ERROR", "MAY_HAVE_EXECUTED"),
])
def test_migration_distinguishes_completed_and_uncertain_calls(status, state):
    db, _ = legacy()
    db.execute("UPDATE llm_prompt_audit SET status = ?", (status,))
    db.commit()
    migrate_llm_audit(db, ASSET)
    metadata = json.loads(db.execute("SELECT metadata_json FROM llm_prompt_audit").fetchone()[0])
    assert metadata == {"legacy": True, "dispatch_state": state}
    db.close()


def test_migration_repairs_original_upgrade_without_touching_new_calls():
    db, expected = legacy()
    migrate_llm_audit(db, ASSET)
    db.execute("UPDATE llm_prompt_audit SET metadata_json = ?", (
        '{"legacy":true,"dispatch_state":"MAY_HAVE_EXECUTED","retained":"fixture"}',))
    db.commit()
    migrate_llm_audit(db, ASSET)
    assert db.execute("SELECT " + ",".join(LEGACY_COLUMNS) + " FROM llm_prompt_audit").fetchone() == expected
    assert json.loads(db.execute("SELECT metadata_json FROM llm_prompt_audit").fetchone()[0]) == {
        "legacy": True, "dispatch_state": "RESPONSE_RECEIVED", "retained": "fixture"}
    db.execute("UPDATE llm_prompt_audit SET metadata_json = ?", ('{"dispatch_state":"MAY_HAVE_EXECUTED"}',))
    db.commit()
    migrate_llm_audit(db, ASSET)
    assert json.loads(db.execute("SELECT metadata_json FROM llm_prompt_audit").fetchone()[0]) == {
        "dispatch_state": "MAY_HAVE_EXECUTED"}
    db.close()
