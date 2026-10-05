CREATE TABLE IF NOT EXISTS review_transaction_ledger_allocation /* Review、Fact、Ledger的不可变三元金额分配 */ (
 id INTEGER PRIMARY KEY, review_id INTEGER NOT NULL DEFAULT 0, transaction_id INTEGER NOT NULL DEFAULT 0,
 ledger_id INTEGER NOT NULL DEFAULT 0, cash_amount INTEGER NOT NULL DEFAULT 0, cash_currency_code TEXT NOT NULL DEFAULT '',
 created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')),
 updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')),
 UNIQUE(ledger_id)
);
CREATE INDEX IF NOT EXISTS ix_review_allocation_case_id ON review_transaction_ledger_allocation(review_id,id);
CREATE INDEX IF NOT EXISTS ix_review_allocation_fact_case ON review_transaction_ledger_allocation(transaction_id,review_id,id);
CREATE INDEX IF NOT EXISTS ix_default_review_lookup ON review_transaction_ledger_allocation(transaction_id,review_id);
