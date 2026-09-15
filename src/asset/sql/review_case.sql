PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS review_case /* 一次完整的交易行为解释及其当前生命周期状态 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    behavior_type INTEGER NOT NULL DEFAULT 0 /* 行为类型：0=DEFAULT，1=CLASSIFICATION，2=AA，3=ADVANCE，4=LOAN_BORROW，5=LOAN_LEND，6=REFUND，7=TRANSFER，8=FX_EXCHANGE，9=DUPLICATE */ CHECK (behavior_type IN (0, 1, 2, 3, 4, 5, 6, 7, 8, 9)),
    status INTEGER NOT NULL DEFAULT 0 /* 生命周期：0=DRAFT，1=CONFIRMED，2=REVOKED */ CHECK (status IN (0, 1, 2)),
    version INTEGER NOT NULL DEFAULT 1 /* 乐观并发版本；每次成功写入递增 */ CHECK (version > 0),
    description TEXT NOT NULL DEFAULT '' /* 对本次流水行为的解释；列表可据此生成展示文本 */,
    detail_json TEXT DEFAULT NULL /* 少量行为专属属性 JSON；禁止保存成员 ID 数组 */ CHECK (detail_json IS NULL OR json_valid(detail_json)),
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 创建时间，UTC ISO-8601 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 最后一次成功状态变更时间，UTC ISO-8601 */
);
