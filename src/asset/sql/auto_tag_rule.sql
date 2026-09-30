PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS auto_tag_rule /* 一个 View 下独立扫描并产生标签候选的规则 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    name TEXT NOT NULL /* 用户可见名称；身份与关联只使用 id */,
    view_id INTEGER NOT NULL CHECK (view_id > 0) /* tag_view.id，逻辑外键；由 Service 批量校验 */,
    method INTEGER NOT NULL DEFAULT 1 CHECK (method = 1) /* 方法：1=LLM_DIRECT */,
    method_config_json TEXT NOT NULL /* schema_version=1 的方法配置 JSON */,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)) /* 是否进入调度：0=否，1=是 */,
    cron TEXT NOT NULL DEFAULT '' /* Cron 表达式；未配置时为空串 */,
    amount_mode INTEGER NOT NULL DEFAULT 1 CHECK (amount_mode IN (1, 2, 3)) /* 金额披露：1=BAND，2=EXACT，3=NONE */,
    rule_revision INTEGER NOT NULL DEFAULT 1 CHECK (rule_revision >= 1) /* 规则内容版本 */,
    scan_after_ledger_id INTEGER NOT NULL DEFAULT 0 CHECK (scan_after_ledger_id >= 0) /* 已完成检查的最后 Ledger ID */,
    scan_epoch INTEGER NOT NULL DEFAULT 1 CHECK (scan_epoch >= 1) /* 游标代次；游标回退时递增 */,
    analyzed_count BIGINT NOT NULL DEFAULT 0 CHECK (analyzed_count BETWEEN 0 AND 9223372036854775807) /* 累计完成分析次数 */,
    failed_count BIGINT NOT NULL DEFAULT 0 CHECK (failed_count BETWEEN 0 AND 9223372036854775807) /* 累计执行失败次数 */,
    suggested_count BIGINT NOT NULL DEFAULT 0 CHECK (suggested_count BETWEEN 0 AND 9223372036854775807) /* 累计生成请求条数 */,
    accepted_count BIGINT NOT NULL DEFAULT 0 CHECK (accepted_count BETWEEN 0 AND 9223372036854775807) /* 累计首次通过条数 */,
    rejected_count BIGINT NOT NULL DEFAULT 0 CHECK (rejected_count BETWEEN 0 AND 9223372036854775807) /* 累计显式拒绝条数 */,
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* 创建时间，UTC ISO-8601 微秒格式 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* 最后更新时间，UTC ISO-8601 微秒格式 */
);

CREATE INDEX IF NOT EXISTS ix_auto_tag_rule_view_id
ON auto_tag_rule (view_id);
