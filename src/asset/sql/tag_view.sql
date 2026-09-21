PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS tag_view /* 标签系统中的一个独立维度 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    name TEXT NOT NULL DEFAULT '' /* 用户可见的维度名称 */,
    system_name TEXT NOT NULL DEFAULT '' /* 稳定且唯一的维度代码 */,
    status TEXT NOT NULL DEFAULT 'ACTIVE' /* 状态：ACTIVE 或 ARCHIVED */,
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* 创建时间，UTC ISO-8601 微秒格式 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* 最后更新时间，UTC ISO-8601 微秒格式 */,
    UNIQUE (system_name)
);
