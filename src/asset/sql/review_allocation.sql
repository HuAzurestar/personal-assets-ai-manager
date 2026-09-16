PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS review_allocation /* Review、Transaction Fact 与 Ledger Entry 的已发布三元金额关系 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    review_case_id INTEGER NOT NULL DEFAULT 0 /* review_case.id 逻辑外键；必须引用已存在 Review */ CHECK (review_case_id > 0),
    transaction_fact_id INTEGER NOT NULL DEFAULT 0 /* transaction_fact.id 逻辑外键；必须引用已存在 Fact */ CHECK (transaction_fact_id > 0),
    ledger_entry_id INTEGER NOT NULL DEFAULT 0 /* ledger_entry.id 逻辑外键；草稿不入库，因此必须为正数 */ CHECK (ledger_entry_id > 0),
    amount_value INTEGER NOT NULL DEFAULT 0 /* 本次分配的正整数金额 */ CHECK (amount_value > 0),
    amount_scale INTEGER NOT NULL DEFAULT 2 /* 分配金额小数位数 */ CHECK (amount_scale BETWEEN 0 AND 8),
    currency_code TEXT NOT NULL DEFAULT '' /* 分配币种；必须与 Fact 和 Ledger Entry 一致 */ CHECK (currency_code <> ''),
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 创建时间，UTC ISO-8601 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 最后更新时间；已发布分配原则上不可变 */,
    UNIQUE (ledger_entry_id) /* 一条 Ledger Entry 必须且只能对应一个 Fact */
);

CREATE INDEX IF NOT EXISTS ix_review_allocation_case_id
    ON review_allocation (review_case_id, id);

CREATE INDEX IF NOT EXISTS ix_review_allocation_fact_case
    ON review_allocation (transaction_fact_id, review_case_id, id);
