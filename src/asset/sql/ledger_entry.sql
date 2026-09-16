PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS ledger_entry /* 已确认 Review 发布的单方向、单币种现金流水投影 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    entry_type INTEGER NOT NULL /* 账本类型：0=TRANSACTION，1=ACCOUNT_TRANSFER，2=CLAIM_CASHFLOW */ CHECK (entry_type IN (0, 1, 2)),
    entry_direction INTEGER NOT NULL /* 资金方向：1=IN，2=OUT */ CHECK (entry_direction IN (1, 2)),
    amount_value INTEGER NOT NULL /* 按 amount_scale 表示的正整数金额 */ CHECK (amount_value > 0),
    amount_scale INTEGER NOT NULL DEFAULT 2 /* 金额小数位数 */ CHECK (amount_scale BETWEEN 0 AND 8),
    currency_code TEXT NOT NULL /* ISO 4217 币种代码 */ CHECK (currency_code <> ''),
    account_code TEXT NOT NULL /* 本条流水对应的本方账户代码 */ CHECK (account_code <> ''),
    counterparty_account_ref TEXT NOT NULL DEFAULT '' /* 对手方账户引用；未知时为空串 */,
    projection_version INTEGER NOT NULL DEFAULT 1 /* Ledger 直接状态的乐观锁版本 */ CHECK (projection_version >= 1),
    occurred_time TEXT NOT NULL /* 现金流发生时间，ISO-8601；禁止用默认时间伪造 */ CHECK (occurred_time <> ''),
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 创建时间，UTC ISO-8601 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 最后一次投影更新时间，UTC ISO-8601 */
);

CREATE INDEX IF NOT EXISTS ix_ledger_entry_occurred_time_id
    ON ledger_entry (occurred_time, id);
