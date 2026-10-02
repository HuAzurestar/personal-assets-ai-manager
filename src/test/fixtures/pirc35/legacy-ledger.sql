-- Structural snapshot of the pre-PIRC-35 ledger, containing no user data.
CREATE TABLE ledger_entry (
 id INTEGER PRIMARY KEY, entry_type INTEGER NOT NULL, entry_direction INTEGER NOT NULL,
 amount INTEGER NOT NULL, currency_code TEXT NOT NULL, account_code TEXT NOT NULL,
 counterparty_account_ref TEXT NOT NULL DEFAULT '', occurred_time TEXT NOT NULL,
 created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')),
 updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now'))
);
CREATE INDEX ix_ledger_entry_occurred_time_id ON ledger_entry(occurred_time,id);
