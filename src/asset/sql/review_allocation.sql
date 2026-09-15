PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS review_allocation /* Review、Transaction Fact 与 Ledger Entry 的三元金额分配 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    review_case_id INTEGER NOT NULL /* review_case.id，逻辑外键；由 Service 批量校验 */,
    transaction_fact_id INTEGER NOT NULL /* transaction_fact.id，逻辑外键；由 Service 批量校验 */,
    ledger_entry_id INTEGER NOT NULL /* ledger_entry.id，逻辑外键；由 Service 批量校验 */,
    amount_value INTEGER NOT NULL /* 本次分配的正整数金额 */ CHECK (amount_value > 0),
    amount_scale INTEGER NOT NULL DEFAULT 2 /* 分配金额小数位数 */ CHECK (amount_scale BETWEEN 0 AND 8),
    currency_code TEXT NOT NULL /* 分配币种，必须与 Fact 和 Ledger Entry 一致 */ CHECK (currency_code <> ''),
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 创建时间，UTC ISO-8601 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 最后更新时间；已确认分配原则上不可变 */,
    UNIQUE (ledger_entry_id) /* 一条 Ledger Entry 只允许对应一个 Transaction Fact */
);

CREATE INDEX IF NOT EXISTS ix_review_allocation_case_id
    ON review_allocation (review_case_id, id);

CREATE INDEX IF NOT EXISTS ix_review_allocation_fact_case
    ON review_allocation (transaction_fact_id, review_case_id, id);
