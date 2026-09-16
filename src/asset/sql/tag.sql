PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS tag /* 标签维度中的一个可选标签值 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    tag_view_id INTEGER NOT NULL /* tag_view.id，逻辑外键；由 Service 批量校验 */,
    name TEXT NOT NULL /* 用户可见的标签名称 */ CHECK (name <> ''),
    code TEXT NOT NULL /* 维度内稳定且唯一的标签代码 */ CHECK (code <> ''),
    status INTEGER NOT NULL DEFAULT 0 /* 状态：0=ACTIVE，1=ARCHIVED */ CHECK (status IN (0, 1)),
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 创建时间，UTC ISO-8601 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 最后更新时间，UTC ISO-8601 */,
    UNIQUE (tag_view_id, code)
);
