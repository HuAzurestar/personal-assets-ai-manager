PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS tag_view /* 标签系统中的一个独立维度 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    name TEXT NOT NULL /* 用户可见的维度名称 */ CHECK (name <> ''),
    code TEXT NOT NULL /* 稳定且唯一的维度代码 */ CHECK (code <> ''),
    status INTEGER NOT NULL DEFAULT 0 /* 状态：0=ACTIVE，1=ARCHIVED */ CHECK (status IN (0, 1)),
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 创建时间，UTC ISO-8601 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 最后更新时间，UTC ISO-8601 */,
    UNIQUE (code)
);
