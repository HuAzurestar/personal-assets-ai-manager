CREATE TABLE IF NOT EXISTS ledger_account /* 个人的管理账户集合 */ (
 id INTEGER PRIMARY KEY, party_id INTEGER NOT NULL, name TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'ACTIVE', statement_interval_months INTEGER NOT NULL DEFAULT 0,
 snapshot_interval_months INTEGER NOT NULL DEFAULT 0,
 created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')),
 updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now'))
);
CREATE INDEX IF NOT EXISTS ledger_account_party_lookup ON ledger_account(party_id,id);
