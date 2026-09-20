PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS review_revision /* Review 的追加式变更、撤销、恢复与审计记录 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，同时作为单机串行写入下的修订顺序 */,
    review_case_id INTEGER NOT NULL DEFAULT 0 /* review_case.id 逻辑外键；必须引用已存在 Review */,
    operation INTEGER NOT NULL DEFAULT 0 /* 操作：0=CREATE，1=UPDATE，2=REVOKE，3=RESTORE */,
    request_json TEXT DEFAULT NULL /* 规范化命令 JSON，用于幂等比较和审计 */,
    before_json TEXT DEFAULT NULL /* 操作前完整 Review 聚合快照 JSON */,
    after_json TEXT DEFAULT NULL /* 操作后完整 Review 聚合快照 JSON */,
    actor TEXT NOT NULL DEFAULT '' /* 发起操作的用户或系统主体 */,
    reason TEXT NOT NULL DEFAULT '' /* 操作原因 */,
    idempotency_key TEXT NOT NULL DEFAULT '' /* 非空时全局唯一的命令幂等键 */,
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 操作发生时间，UTC ISO-8601；记录只追加不更新 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 保留统一字段；追加式记录不应更新 */
);

CREATE INDEX IF NOT EXISTS ix_review_revision_case_id
    ON review_revision (review_case_id, id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_review_revision_idempotency_key
    ON review_revision (idempotency_key)
    WHERE idempotency_key <> '';
