PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS ledger_entry /* 已确认 Review 发布的单方向、单币种现金流水投影 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    entry_type INTEGER NOT NULL /* 账本类型：0=INCOME_AND_EXPENSE，1=INTERNAL_TRANSFER，2=ASSET_AND_LIABILITY */,
    entry_direction INTEGER NOT NULL /* 资金方向：1=IN，2=OUT */,
    amount INTEGER NOT NULL /* 按 currency_code 对应最小单位表示的正整数金额 */,
    currency_code TEXT NOT NULL /* 币种与精度单位；CNY=0.01，CNY_4=0.0001 */,
    account_code TEXT NOT NULL /* 本条流水对应的本方账户代码 */,
    counterparty_account_ref TEXT NOT NULL DEFAULT '' /* 对手方账户引用；未知时为空串 */,
    occurred_time TEXT NOT NULL /* 现金流发生时间，UTC ISO-8601；禁止用默认时间伪造 */,
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* 创建时间，UTC ISO-8601 微秒格式 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* 最后一次投影更新时间，UTC ISO-8601 微秒格式 */
);

CREATE INDEX IF NOT EXISTS ix_ledger_entry_occurred_time_id
    ON ledger_entry (occurred_time, id);
