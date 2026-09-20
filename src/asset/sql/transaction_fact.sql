PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS transaction_fact /* 接受后的不可变规范交易事实 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    fact_key TEXT NOT NULL /* 跨来源稳定且唯一的事实身份键 */,
    occurred_time TEXT NOT NULL /* 交易发生时间，UTC ISO-8601；禁止用默认时间伪造 */,
    cash_direction INTEGER NOT NULL /* 资金方向：1=CASH_DIRECTION_IN，2=CASH_DIRECTION_OUT */,
    amount INTEGER NOT NULL /* 按 currency_code 对应最小单位表示的正整数金额 */,
    currency_code TEXT NOT NULL /* 币种与精度单位；CNY=0.01，CNY_4=0.0001 */,
    account_code TEXT NOT NULL /* 本方交易账户的稳定代码 */,
    counterparty_name TEXT NOT NULL DEFAULT '' /* 规范化交易对手名称 */,
    counterparty_account_ref TEXT NOT NULL DEFAULT '' /* 来源可识别的对手方账户引用；未知时为空串 */,
    summary TEXT NOT NULL DEFAULT '' /* 不包含原始证据的规范交易摘要 */,
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 创建时间，UTC ISO-8601 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 保留统一字段；事实业务字段不可变 */,
    UNIQUE (fact_key)
);

CREATE INDEX IF NOT EXISTS ix_transaction_fact_occurred_time_id
    ON transaction_fact (occurred_time, id);
