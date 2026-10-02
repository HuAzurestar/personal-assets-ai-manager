CREATE TABLE IF NOT EXISTS review_ledger_position_leg_allocation /* 现金与数量腿的归因；不重复计现金 */ (
 id INTEGER PRIMARY KEY, review_id INTEGER NOT NULL, ledger_id INTEGER NOT NULL,
 position_leg_id INTEGER NOT NULL, cash_amount INTEGER NOT NULL, cash_currency_code TEXT NOT NULL,
 created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')),
 updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')),
 UNIQUE(ledger_id,position_leg_id)
);
CREATE INDEX IF NOT EXISTS ix_position_allocation_review ON review_ledger_position_leg_allocation(review_id,id);
CREATE INDEX IF NOT EXISTS ix_position_allocation_leg ON review_ledger_position_leg_allocation(position_leg_id,id);
