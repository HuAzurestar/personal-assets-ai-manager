"""Fixed, offline PIRC-35 migration SQL and conservation checks."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

NEW_TABLES = ("ledger_account_party", "ledger_account", "ledger_account_ref",
              "position", "position_leg", "review_ledger_position_leg_allocation")
ALLOCATION_RENAME = {"review_case_id": "review_id", "transaction_fact_id": "transaction_id",
                     "ledger_entry_id": "ledger_id", "amount": "cash_amount",
                     "currency_code": "cash_currency_code"}


def quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


class SchemaMigrationMapper:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def preflight(self) -> set[int]:
        c = self.connection
        if c.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("DATABASE_INTEGRITY_FAILED")
        tables = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "review_allocation" not in tables or "review_transaction_ledger_allocation" in tables:
            raise ValueError("LEGACY_SCHEMA_REQUIRED")
        broken = c.execute("""
            SELECT a.id FROM review_allocation a
            LEFT JOIN review_case r ON r.id=a.review_case_id
            LEFT JOIN transaction_fact f ON f.id=a.transaction_fact_id
            LEFT JOIN ledger_entry l ON l.id=a.ledger_entry_id
            WHERE r.id IS NULL OR f.id IS NULL OR l.id IS NULL OR a.amount<=0
               OR a.amount!=l.amount OR a.currency_code!=l.currency_code
               OR a.currency_code!=f.currency_code OR l.entry_direction!=f.cash_direction
               OR l.occurred_time!=f.occurred_time LIMIT 1
        """).fetchone()
        if broken:
            raise ValueError("RELATION_BROKEN")
        if c.execute("SELECT id FROM transaction_fact WHERE amount<=0 OR cash_direction NOT IN (1,2) LIMIT 1").fetchone():
            raise ValueError("RELATION_BROKEN")
        if c.execute("""SELECT row.id FROM transaction_import_row row
            LEFT JOIN transaction_import_file file ON file.id=row.transaction_import_file_id
            LEFT JOIN transaction_fact f ON f.id=row.transaction_fact_id
            WHERE file.id IS NULL OR (row.transaction_fact_id>0 AND f.id IS NULL)
                OR (row.row_status=1 AND row.transaction_fact_id<=0) LIMIT 1""").fetchone():
            raise ValueError("SOURCE_RELATION_BROKEN")
        if c.execute("""SELECT l.id FROM ledger_entry l LEFT JOIN review_allocation a
            ON a.ledger_entry_id=l.id GROUP BY l.id HAVING COUNT(a.id)!=1 LIMIT 1""").fetchone():
            raise ValueError("RELATION_BROKEN")
        if c.execute("""SELECT f.id FROM transaction_fact f
            LEFT JOIN review_allocation a ON a.transaction_fact_id=f.id
            LEFT JOIN review_case r ON r.id=a.review_case_id
            GROUP BY f.id HAVING COALESCE(SUM(CASE WHEN r.status=0 THEN a.amount ELSE 0 END),0)!=f.amount
            LIMIT 1""").fetchone():
            raise ValueError("LEGACY_COVERAGE_REVIEW_REQUIRED")
        if c.execute("""SELECT t.id FROM ledger_entry_tag t LEFT JOIN ledger_entry l ON l.id=t.ledger_id
            LEFT JOIN tag ON tag.id=t.tag_id LEFT JOIN tag_view v ON v.id=tag.view_id
            WHERE l.id IS NULL OR tag.id IS NULL OR v.id IS NULL LIMIT 1""").fetchone():
            raise ValueError("TAG_RELATION_BROKEN")
        originals = set()
        if "review_revision" in tables:
            for review_id, request in c.execute("SELECT review_case_id,request_json FROM review_revision WHERE operation=0 AND actor='system'"):
                try:
                    command = json.loads(request or "{}")
                except (TypeError, ValueError):
                    continue
                if isinstance(command, dict) and command.get("operation") == "AUTO_REVIEW":
                    originals.add(review_id)
        candidates = {}
        review_sizes = dict(c.execute("SELECT review_case_id,COUNT(id) FROM review_allocation GROUP BY review_case_id"))
        for row in c.execute("""SELECT f.id,r.id,a.amount,f.amount,a.currency_code,f.currency_code,l.entry_type
            FROM transaction_fact f JOIN review_allocation a ON a.transaction_fact_id=f.id
            JOIN review_case r ON r.id=a.review_case_id JOIN ledger_entry l ON l.id=a.ledger_entry_id"""):
            fact_id, review_id, amount, fact_amount, currency, fact_currency, entry_type = row
            if review_id in originals and review_sizes[review_id] == 1 and amount == fact_amount and currency == fact_currency and entry_type == 0:
                candidates.setdefault(fact_id, []).append(review_id)
        facts = {row[0] for row in c.execute("SELECT id FROM transaction_fact")}
        if set(candidates) != facts or any(len(values) != 1 for values in candidates.values()):
            raise ValueError("DEFAULT_IDENTITY_REQUIRED")
        return {values[0] for values in candidates.values()}

    def manifest(self, *, mapped: bool = False, defaults: set[int] | None = None, original=None):
        c = self.connection
        tables = original or {
            name: [row[1] for row in c.execute(f"PRAGMA table_info({quoted(name)})")]
            for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        }
        result = {}
        for name, columns in tables.items():
            target = "review_transaction_ledger_allocation" if mapped and name == "review_allocation" else name
            mapping = ALLOCATION_RENAME if name == "review_allocation" else {"amount": "cash_amount", "currency_code": "cash_currency_code"} if name == "ledger_entry" else {}
            select_columns = [mapping.get(column, column) if mapped else column for column in columns]
            digest = hashlib.sha256()
            count = 0
            for row in c.execute(f"SELECT {','.join(map(quoted, select_columns))} FROM {quoted(target)} ORDER BY id"):
                values = list(row)
                if not mapped and defaults is not None and name == "review_case":
                    index = columns.index("behavior_type")
                    if values[columns.index("id")] in defaults:
                        values[index] = 0
                    elif values[index] == 0 or values[index] not in {1, 2, 3, 4}:
                        values[index] = 4
                digest.update(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode())
                digest.update(b"\n")
                count += 1
            result[name] = (count, digest.hexdigest())
        return tables, result

    def migrate(self, assets: Path, defaults: set[int], table_names) -> None:
        c = self.connection
        c.execute("BEGIN IMMEDIATE")
        c.execute("ALTER TABLE review_allocation RENAME TO review_transaction_ledger_allocation")
        for old, new in ALLOCATION_RENAME.items():
            c.execute(f"ALTER TABLE review_transaction_ledger_allocation RENAME COLUMN {quoted(old)} TO {quoted(new)}")
        c.execute("ALTER TABLE ledger_entry RENAME COLUMN amount TO cash_amount")
        c.execute("ALTER TABLE ledger_entry RENAME COLUMN currency_code TO cash_currency_code")
        c.execute("ALTER TABLE ledger_entry ADD COLUMN account_ref_id INTEGER NOT NULL DEFAULT 0")
        c.execute("UPDATE review_case SET behavior_type=4 WHERE behavior_type=0 OR behavior_type NOT IN (1,2,3,4)")
        c.executemany("UPDATE review_case SET behavior_type=0 WHERE id=?", [(value,) for value in sorted(defaults)])
        for name in table_names:
            statement = ""
            for line in (assets / f"{name}.sql").read_text(encoding="utf-8").splitlines(keepends=True):
                statement += line
                if sqlite3.complete_statement(statement):
                    c.execute(statement)
                    statement = ""
            if statement.strip():
                c.execute(statement)
