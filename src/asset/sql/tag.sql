PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS tag /* 标签维度中的一个可选标签值 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    view_id INTEGER NOT NULL DEFAULT 0 /* tag_view.id，逻辑外键；由 Service 批量校验 */,
    name TEXT NOT NULL DEFAULT '' /* 用户可见的标签名称 */,
    system_name TEXT NOT NULL DEFAULT '' /* 维度内稳定且唯一的标签代码 */,
    status TEXT NOT NULL DEFAULT 'ACTIVE' /* 状态：ACTIVE 或 ARCHIVED */,
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* 创建时间，UTC ISO-8601 微秒格式 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* 最后更新时间，UTC ISO-8601 微秒格式 */,
    UNIQUE (view_id, system_name)
);
