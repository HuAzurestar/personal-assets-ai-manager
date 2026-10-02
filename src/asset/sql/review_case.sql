PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS review_case /* 一次已发布流水行为解释的当前状态 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    behavior_type INTEGER NOT NULL DEFAULT 0 /* 0=系统NORMAL_TRANSACTION，1=BORROW_AND_REPAY，2=CREDIT_CARD，3=SHARED_SETTLEMENT，4=OTHER_MANUAL */,
    status INTEGER NOT NULL DEFAULT 0 /* 生命周期：0=CONFIRMED，1=REVOKED */,
    title TEXT NOT NULL DEFAULT '' /* 用户可读标题；允许空标题但不承载结构化详情 */,
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* 创建时间，UTC ISO-8601 微秒格式 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* 最后一次启停时间；发布后标题和内容不可原位改写 */
);
