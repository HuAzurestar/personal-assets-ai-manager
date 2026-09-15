PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS transaction_import_row /* 来源文件中的一行不可变交易证据 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    transaction_fact_id INTEGER NOT NULL DEFAULT 0 /* 关联的 transaction_fact.id；0=尚未解决或未接受 */ CHECK (transaction_fact_id >= 0),
    transaction_import_file_id INTEGER NOT NULL /* 所属 transaction_import_file.id，逻辑外键；由 Service 批量校验 */,
    source_row_number INTEGER NOT NULL /* 来源文件中的一基行号 */ CHECK (source_row_number > 0),
    source_reference TEXT NOT NULL DEFAULT '' /* 来源交易号、订单号或流水号 */,
    raw_payload TEXT DEFAULT NULL /* 原始字段及规范化结果 JSON；JSON 允许 NULL */ CHECK (raw_payload IS NULL OR json_valid(raw_payload)),
    raw_hash TEXT NOT NULL DEFAULT '' /* 规范化来源行的 SHA-256 指纹 */,
    parse_status INTEGER NOT NULL DEFAULT 0 /* 处理状态：0=PENDING，1=SUCCESS，2=DUPLICATE，3=SKIPPED，4=INVALID */ CHECK (parse_status IN (0, 1, 2, 3, 4)),
    issue_code TEXT NOT NULL DEFAULT '' /* 稳定的机器错误代码 */,
    issue_message TEXT NOT NULL DEFAULT '' /* 用户可读的错误说明 */,
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 创建时间，UTC ISO-8601 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 仅处理状态和关联变化时更新 */,
    UNIQUE (transaction_import_file_id, source_row_number)
);

CREATE INDEX IF NOT EXISTS ix_transaction_import_row_fact_id
    ON transaction_import_row (transaction_fact_id, id);

CREATE INDEX IF NOT EXISTS ix_transaction_import_row_reference_fact
    ON transaction_import_row (source_reference, transaction_fact_id)
    WHERE source_reference <> '';
