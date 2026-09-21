PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS review_case /* 一次已发布流水行为解释的当前状态 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    behavior_type INTEGER NOT NULL DEFAULT 0 /* 行为类型：0=NORMAL_TRANSACTION，1=BORROW_AND_REPAY */,
    status INTEGER NOT NULL DEFAULT 0 /* 生命周期：0=CONFIRMED，1=REVOKED */,
    title TEXT NOT NULL DEFAULT '' /* 用户可读标题；允许空标题但不承载结构化详情 */,
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* 创建时间，UTC ISO-8601 微秒格式 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* 最后一次状态或标题变更时间，UTC ISO-8601 微秒格式 */
);
