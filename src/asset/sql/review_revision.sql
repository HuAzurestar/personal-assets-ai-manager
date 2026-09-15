PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS review_revision /* Review 的追加式变更、撤销、幂等与审计记录 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    review_case_id INTEGER NOT NULL /* review_case.id，逻辑外键；由 Service 批量校验 */,
    version INTEGER NOT NULL /* 本次操作完成后的 Review 版本 */ CHECK (version > 0),
    operation INTEGER NOT NULL /* 操作：0=CREATE，1=UPDATE，2=CONFIRM，3=REVOKE，4=RESTORE，5=AUTO_REPLACE，6=AUTO_RESIDUAL，7=AUTO_RESTORE */ CHECK (operation IN (0, 1, 2, 3, 4, 5, 6, 7)),
    schema_version INTEGER NOT NULL DEFAULT 1 /* 快照和命令载荷的结构版本 */ CHECK (schema_version > 0),
    request_json TEXT DEFAULT NULL /* 规范化命令 JSON，用于幂等比较和审计 */ CHECK (request_json IS NULL OR json_valid(request_json)),
    before_json TEXT DEFAULT NULL /* 操作前完整 Review 聚合快照 JSON */ CHECK (before_json IS NULL OR json_valid(before_json)),
    after_json TEXT DEFAULT NULL /* 操作后完整 Review 聚合快照 JSON */ CHECK (after_json IS NULL OR json_valid(after_json)),
    snapshot_hash TEXT NOT NULL DEFAULT '' /* after_json 的 SHA-256 完整性指纹 */,
    reverses_revision_id INTEGER NOT NULL DEFAULT 0 /* 本操作反向对应的 review_revision.id；0=无 */ CHECK (reverses_revision_id >= 0),
    actor TEXT NOT NULL DEFAULT '' /* 发起操作的用户或系统主体 */,
    reason TEXT NOT NULL DEFAULT '' /* 操作原因 */,
    idempotency_key TEXT NOT NULL DEFAULT '' /* 非空时全局唯一的命令幂等键 */,
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 操作发生时间，UTC ISO-8601；记录只追加不更新 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 保留统一字段，追加式记录不应更新 */,
    UNIQUE (review_case_id, version)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_review_revision_idempotency_key
    ON review_revision (idempotency_key)
    WHERE idempotency_key <> '';
