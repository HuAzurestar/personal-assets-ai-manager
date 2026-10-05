"""Fixed, offline PIRC-35 migration SQL and conservation checks."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import re
from pathlib import Path
from backend.core.stored_timestamp import canonical_timestamp, check_timestamps
from backend.core.money import normalize_currency_code, MAX_ABS_AMOUNT
from backend.core.source_account_identity import reliable_source

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
        if set(NEW_TABLES) & tables:
            raise ValueError('LEGACY_SCHEMA_REQUIRED')
        for table in ('transaction_fact','ledger_entry'):
            for amount,currency in c.execute(f'SELECT amount,currency_code FROM "{table}"'):
                if type(amount) is not int or not 0 < amount <= MAX_ABS_AMOUNT:
                    raise ValueError('RELATION_BROKEN')
                try:
                    valid_currency = normalize_currency_code(currency) == currency
                except ValueError:
                    valid_currency = False
                if not valid_currency:
                    raise ValueError('RELATION_BROKEN')
        if c.execute('SELECT id FROM review_case WHERE status NOT IN (0,1) LIMIT 1').fetchone():
            raise ValueError('RELATION_BROKEN')
        if c.execute('SELECT id FROM ledger_entry WHERE entry_type NOT IN (0,1,2) LIMIT 1').fetchone():
            raise ValueError('RELATION_BROKEN')
        broken = c.execute("""
            SELECT a.id FROM review_allocation a
            LEFT JOIN review_case r ON r.id=a.review_case_id
            LEFT JOIN transaction_fact f ON f.id=a.transaction_fact_id
            LEFT JOIN ledger_entry l ON l.id=a.ledger_entry_id
            WHERE r.id IS NULL OR f.id IS NULL OR l.id IS NULL OR a.amount<=0
               OR a.amount!=l.amount OR a.currency_code!=l.currency_code
               OR a.currency_code!=f.currency_code OR l.entry_direction!=f.cash_direction
               LIMIT 1
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
            GROUP BY f.id HAVING COALESCE(SUM(CASE WHEN r.status=0 THEN a.amount ELSE 0 END),0)>f.amount
            LIMIT 1""").fetchone():
            raise ValueError("LEGACY_COVERAGE_REVIEW_REQUIRED")
        if c.execute("""SELECT t.id FROM ledger_entry_tag t LEFT JOIN ledger_entry l ON l.id=t.ledger_id
            LEFT JOIN tag ON tag.id=t.tag_id LEFT JOIN tag_view v ON v.id=tag.view_id
            WHERE l.id IS NULL OR tag.id IS NULL OR v.id IS NULL LIMIT 1""").fetchone():
            raise ValueError("TAG_RELATION_BROKEN")
        originals = {}
        # Canonical instant equivalence is checked before comparing source texts.
        for fact_time,ledger_time in c.execute('''SELECT f.occurred_time,l.occurred_time
            FROM review_allocation a JOIN transaction_fact f ON f.id=a.transaction_fact_id
            JOIN ledger_entry l ON l.id=a.ledger_entry_id'''):
            if canonical_timestamp(fact_time) != canonical_timestamp(ledger_time):
                raise ValueError('RELATION_BROKEN')
        if "review_revision" in tables:
            for review_id, request in c.execute("SELECT review_case_id,request_json FROM review_revision WHERE operation=0 AND actor='system'"):
                if request is not None and len(request.encode('utf-8')) > 1024*1024:
                    raise ValueError('DEFAULT_IDENTITY_REQUIRED')
                try:
                    command = json.loads(request or "{}")
                except (TypeError, ValueError):
                    continue
                if isinstance(command, dict) and command.get("operation") == "AUTO_REVIEW" and type(command.get('fact_id')) is int:
                    originals.setdefault(review_id,set()).add(command['fact_id'])
        candidates = {}
        review_sizes = dict(c.execute("SELECT review_case_id,COUNT(id) FROM review_allocation GROUP BY review_case_id"))
        for row in c.execute("""SELECT f.id,r.id,a.amount,f.amount,a.currency_code,f.currency_code,l.entry_type
            FROM transaction_fact f JOIN review_allocation a ON a.transaction_fact_id=f.id
            JOIN review_case r ON r.id=a.review_case_id JOIN ledger_entry l ON l.id=a.ledger_entry_id"""):
            fact_id, review_id, amount, fact_amount, currency, fact_currency, entry_type = row
            if originals.get(review_id) == {fact_id} and review_sizes[review_id] == 1 and amount == fact_amount and currency == fact_currency and entry_type == 0:
                candidates.setdefault(fact_id, []).append(review_id)
        facts = {row[0] for row in c.execute("SELECT id FROM transaction_fact")}
        if set(candidates) != facts or any(len(values) != 1 for values in candidates.values()):
            raise ValueError("DEFAULT_IDENTITY_REQUIRED")
        return {values[0] for values in candidates.values()}

    def coverage_gaps(self, *, mapped=False):
        a = 'review_transaction_ledger_allocation' if mapped else 'review_allocation'
        rid,fid,amount = ('review_id','transaction_id','cash_amount') if mapped else ('review_case_id','transaction_fact_id','amount')
        return self.connection.execute(f'''SELECT COUNT(*) FROM (SELECT f.id FROM transaction_fact f
            LEFT JOIN "{a}" a ON a."{fid}"=f.id LEFT JOIN review_case r ON r.id=a."{rid}"
            GROUP BY f.id HAVING COALESCE(SUM(CASE WHEN r.status=0 THEN a."{amount}" ELSE 0 END),0)<f.amount)''').fetchone()[0]

    def initialize_reliable_refs(self):
        """Only exact original normalized bank evidence, never account suffixes."""
        c = self.connection
        identities = {}
        for fid,account,amount,direction,currency,occurred,raw,source_code in c.execute('''SELECT f.id,f.account_code,
            f.amount,f.cash_direction,f.currency_code,f.occurred_time,row.raw_payload,file.source_type
            FROM transaction_import_row row JOIN transaction_import_file file ON file.id=row.transaction_import_file_id
            JOIN transaction_fact f ON f.id=row.transaction_fact_id WHERE row.row_status=1 ORDER BY row.id'''):
            if raw is None:
                continue
            if len(raw.encode('utf-8')) > 1024*1024:
                raise ValueError('SOURCE_EVIDENCE_REVIEW_REQUIRED')
            try:
                payload = json.loads(raw)
            except (TypeError,ValueError):
                continue  # Legacy opaque text is conserved, not guessed.
            normalized = payload.get('normalized') if isinstance(payload,dict) else None
            if not isinstance(normalized,dict):
                continue
            if not isinstance(normalized.get('source_account',{}),dict) or not isinstance(normalized.get('account',{}),dict):
                raise ValueError('SOURCE_EVIDENCE_REVIEW_REQUIRED')
            identity = reliable_source(normalized)
            if identity is None:
                continue
            code = {'abc':202,'ccb':201,'cmb':203}.get(normalized.get('source_type'))
            number = normalized.get('account',{}).get('number')
            old_identity = normalized.get('account',{}).get('identity')
            if code != source_code or number != identity[1] or account not in (number,old_identity):
                raise ValueError('SOURCE_EVIDENCE_REVIEW_REQUIRED')
            signed = normalized.get('amount_minor')
            if type(signed) is not int or signed != (amount if direction==1 else -amount) or normalized.get('currency') != currency:
                raise ValueError('SOURCE_EVIDENCE_REVIEW_REQUIRED')
            if canonical_timestamp(normalized.get('occurred_at')) != canonical_timestamp(occurred):
                raise ValueError('SOURCE_EVIDENCE_REVIEW_REQUIRED')
            if fid in identities and identities[fid] != identity:
                raise ValueError('SOURCE_EVIDENCE_REVIEW_REQUIRED')
            identities[fid] = identity
        refs = {}
        for namespace,identity in sorted(set(identities.values())):
            cursor = c.execute('''INSERT INTO ledger_account_ref(account_id,name,institution,reference,source_namespace,
                source_identity,identity_strength,status) VALUES(0,'',?,?,?, ?,1,'ACTIVE')''',
                (namespace.split(':',1)[0],identity,namespace,identity))
            refs[(namespace,identity)] = cursor.lastrowid
        updates = [(refs[identity],fid) for fid,identity in sorted(identities.items())]
        for offset in range(0,len(updates),400):
            c.executemany('''UPDATE ledger_entry SET account_ref_id=? WHERE id IN
                (SELECT ledger_id FROM review_transaction_ledger_allocation WHERE transaction_id=?)''',updates[offset:offset+400])
        return len(refs)

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
                for index,column in enumerate(columns):
                    if column in {'created_time','updated_time','occurred_time'}:
                        values[index] = canonical_timestamp(values[index])
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
        ordered = sorted(defaults)
        for offset in range(0,len(ordered),400):
            c.executemany("UPDATE review_case SET behavior_type=0 WHERE id=?", [(value,) for value in ordered[offset:offset+400]])
        for name in table_names:
            statement = ""
            for line in (assets / f"{name}.sql").read_text(encoding="utf-8").splitlines(keepends=True):
                statement += line
                if sqlite3.complete_statement(statement):
                    c.execute(statement)
                    statement = ""
            if statement.strip():
                c.execute(statement)
        self.timestamp_changes = check_timestamps(c,table_names,normalize=True)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()


def check_expressions(sql):
    """Registered DDL CHECKs, including nested functions; comments are not SQL."""
    sql = re.sub(r'/\*.*?\*/|--[^\n]*','',sql,flags=re.DOTALL)
    result = []
    for match in re.finditer(r'\bCHECK\s*\(',sql,flags=re.IGNORECASE):
        start = match.end()
        depth,quote,index = 1,None,start
        while index < len(sql) and depth:
            char = sql[index]
            if quote:
                if char == quote:
                    if index+1 < len(sql) and sql[index+1] == quote:
                        index += 1
                    else:
                        quote = None
            elif char in "'\"":
                quote = char
            elif char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
            index += 1
        if depth:
            raise ValueError('SCHEMA_MANIFEST_MISMATCH')
        result.append(''.join(sql[start:index-1].split()).replace('"',''))
    return sorted(result)


def schema_profile(connection):
    """Semantic columns/defaults/index definitions, insensitive to rename order."""
    result = {}
    for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        columns = {row[1]:[row[2].upper(),row[3],''.join((row[4] or '').split()),row[5]]
            for row in connection.execute(f'PRAGMA table_info({quoted(table)})')}
        indexes = []
        for row in connection.execute(f'PRAGMA index_list({quoted(table)})'):
            keys = [[key[2],key[3],key[4]] for key in connection.execute(f'PRAGMA index_xinfo({quoted(row[1])})') if key[5]]
            sql = connection.execute('SELECT sql FROM sqlite_master WHERE type="index" AND name=?',(row[1],)).fetchone()[0]
            predicate = ''.join(sql.split('WHERE',1)[1].split()).replace('"','') if row[4] and sql else ''
            indexes.append([row[1] if row[3] != 'u' else '<unique>',row[2],keys,predicate])
        sql = connection.execute('SELECT sql FROM sqlite_master WHERE type="table" AND name=?',(table,)).fetchone()[0]
        result[table] = dict(columns=columns,indexes=sorted(indexes),checks=check_expressions(sql),
            foreign_keys=[list(row) for row in connection.execute(f'PRAGMA foreign_key_list({quoted(table)})')])
    triggers = connection.execute("SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name").fetchall()
    if triggers:
        result['<triggers>'] = triggers
    return result


def expected_schema(assets,table_names):
    with sqlite3.connect(':memory:') as connection:
        for name in table_names:
            connection.executescript((assets / f'{name}.sql').read_text(encoding='utf-8'))
        return schema_profile(connection)
