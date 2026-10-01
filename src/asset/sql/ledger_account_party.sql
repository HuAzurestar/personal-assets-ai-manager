CREATE TABLE IF NOT EXISTS ledger_account_party /* 被管理个人 */ (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ACTIVE',
 created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')),
 updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now'))
);
