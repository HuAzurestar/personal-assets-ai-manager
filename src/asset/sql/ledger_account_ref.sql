CREATE TABLE IF NOT EXISTS ledger_account_ref /* 具体本方资金来源；可尚未归属集合 */ (
 id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL DEFAULT 0, name TEXT NOT NULL DEFAULT '',
 institution TEXT NOT NULL DEFAULT '', reference TEXT NOT NULL DEFAULT '',
 source_namespace TEXT NOT NULL DEFAULT '', source_identity TEXT NOT NULL DEFAULT '',
 identity_strength INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'ACTIVE',
 created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')),
 updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now'))
);
CREATE INDEX IF NOT EXISTS ledger_account_ref_account ON ledger_account_ref(account_id,id);
CREATE UNIQUE INDEX IF NOT EXISTS ledger_account_ref_source_identity
 ON ledger_account_ref(source_namespace,source_identity) WHERE identity_strength=1;
