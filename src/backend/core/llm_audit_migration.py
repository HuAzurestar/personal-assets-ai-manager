"""Transactional rebuild of the one audit table, preserving every old column."""
from __future__ import annotations
import sqlite3

LEGACY_COLUMNS = (
    "id", "run_id", "rule_id", "rule_revision", "ledger_id", "model_id", "attempt",
    "model_name", "request_json", "response_text", "response_truncated", "status",
    "error_code", "created_time", "updated_time",
)


def migrate_llm_audit(driver, asset):
    columns = {row[1] for row in driver.execute("PRAGMA table_info(llm_prompt_audit)")}
    if not columns or "metadata_json" in columns:
        return
    if columns != set(LEGACY_COLUMNS):
        raise RuntimeError("Unrecognized audit schema; migration refused")
    driver.execute("BEGIN IMMEDIATE")
    try:
        driver.execute("ALTER TABLE llm_prompt_audit RENAME TO llm_prompt_audit_legacy")
        # Execute DDL without executescript's implicit commit.
        statement = ""
        for line in asset.read_text(encoding="utf-8").splitlines(keepends=True):
            statement += line
            if sqlite3.complete_statement(statement):
                if not statement.strip().startswith("PRAGMA"):
                    driver.execute(statement)
                statement = ""
        names = ", ".join(LEGACY_COLUMNS)
        driver.execute(f"INSERT INTO llm_prompt_audit ({names}, metadata_json) "
                       f"SELECT {names}, '{{\"dispatch_state\":\"MAY_HAVE_EXECUTED\",\"legacy\":true}}' "
                       "FROM llm_prompt_audit_legacy")
        before = driver.execute("SELECT count(id) FROM llm_prompt_audit_legacy").fetchone()[0]
        after = driver.execute("SELECT count(id) FROM llm_prompt_audit").fetchone()[0]
        if before != after:
            raise RuntimeError("Audit migration row count mismatch")
        driver.execute("DROP TABLE llm_prompt_audit_legacy")
        driver.commit()
    except Exception:
        driver.rollback()
        raise
