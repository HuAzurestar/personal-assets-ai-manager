PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS ledger_entry_tag /* Ledger Entry 与 Tag 的热投影关联 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    ledger_id INTEGER NOT NULL DEFAULT 0 /* ledger_entry.id，逻辑外键；由 Service 批量校验 */,
    tag_id INTEGER NOT NULL /* tag.id，逻辑外键；由 Service 批量校验 */,
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 创建时间，UTC ISO-8601 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 最后更新时间，UTC ISO-8601 */,
    UNIQUE (ledger_id, tag_id)
);
