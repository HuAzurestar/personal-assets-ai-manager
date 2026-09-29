PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS tag_assignment_request /* 自动规则发起、由人工审查的标签请求 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    rule_id INTEGER NOT NULL CHECK (rule_id > 0) /* auto_tag_rule.id，逻辑外键 */,
    rule_revision INTEGER NOT NULL CHECK (rule_revision > 0) /* 生成请求时的规则版本 */,
    ledger_id INTEGER NOT NULL CHECK (ledger_id > 0) /* ledger_entry.id，逻辑外键 */,
    view_id INTEGER NOT NULL CHECK (view_id > 0) /* tag_view.id，逻辑外键 */,
    proposed_tag_id INTEGER NOT NULL CHECK (proposed_tag_id > 0) /* tag.id，逻辑外键 */,
    status INTEGER NOT NULL DEFAULT 1 CHECK (status IN (1, 2, 3, 4, 5)) /* 1=PENDING，2=ENABLED，3=REJECTED，4=CANCELLED，5=REPLACED */,
    reason_summary TEXT NOT NULL DEFAULT '' CHECK (length(reason_summary) <= 200) /* 已清洗理由，最多 200 字 */,
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* 创建时间，UTC ISO-8601 微秒格式 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* 最后更新时间，UTC ISO-8601 微秒格式 */
);

CREATE INDEX IF NOT EXISTS ix_tag_assignment_request_scope_status
ON tag_assignment_request (ledger_id, view_id, status);

CREATE INDEX IF NOT EXISTS ix_tag_assignment_request_rule_status
ON tag_assignment_request (rule_id, status);

CREATE INDEX IF NOT EXISTS ix_tag_assignment_request_created_time_id
ON tag_assignment_request (created_time, id);
