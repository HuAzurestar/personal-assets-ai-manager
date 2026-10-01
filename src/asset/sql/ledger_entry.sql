PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS ledger_entry /* 已确认 Review 发布的单方向、单币种现金流水投影 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    entry_type INTEGER NOT NULL /* 0=TRANSACTION，1=ACCOUNT_TRANSFER，2=ASSET_LIABILITY，3=DUPLICATE */,
    entry_direction INTEGER NOT NULL /* 资金方向：1=IN，2=OUT */,
    cash_amount INTEGER NOT NULL /* 正整数现金金额 */,
    cash_currency_code TEXT NOT NULL /* 币种与精度单位 */,
    account_ref_id INTEGER NOT NULL DEFAULT 0 /* 已知具体来源；0为待识别 */,
    account_code TEXT NOT NULL /* 本条流水对应的本方账户代码 */,
    counterparty_account_ref TEXT NOT NULL DEFAULT '' /* 对手方账户引用；未知时为空串 */,
    occurred_time TEXT NOT NULL /* 现金流发生时间，UTC ISO-8601；禁止用默认时间伪造 */,
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* 创建时间，UTC ISO-8601 微秒格式 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* 最后一次投影更新时间，UTC ISO-8601 微秒格式 */
);

CREATE INDEX IF NOT EXISTS ix_ledger_entry_occurred_time_id
    ON ledger_entry (occurred_time, id);
CREATE INDEX IF NOT EXISTS ledger_entry_account_ref_time
    ON ledger_entry(account_ref_id,cash_currency_code,occurred_time,id);
