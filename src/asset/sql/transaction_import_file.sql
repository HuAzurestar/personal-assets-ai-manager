PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS transaction_import_file /* 一次交易数据导入的来源文件 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    batch_code TEXT NOT NULL DEFAULT '' /* 同一次提交的批次标识 */,
    source_type INTEGER NOT NULL DEFAULT 0 /* 来源类型：0=UNKNOWN，1=ALIPAY，2=WECHAT，3=BANK，4=MANUAL */,
    institution_code TEXT NOT NULL DEFAULT '' /* 银行或平台的稳定机构代码 */,
    filename TEXT NOT NULL /* 用户看到的原始文件名 */ CHECK (filename <> ''),
    file_format INTEGER NOT NULL DEFAULT 0 /* 文件格式：0=UNKNOWN，1=CSV，2=XLS，3=XLSX，4=PDF，5=ZIP */,
    sha256 TEXT NOT NULL /* 整个来源文件的 SHA-256 指纹 */ CHECK (sha256 <> ''),
    period_start TEXT NOT NULL DEFAULT '' /* 文件内最早有效交易时间，ISO-8601；未知时为空串 */,
    period_end TEXT NOT NULL DEFAULT '' /* 文件内最晚有效交易时间，ISO-8601；未知时为空串 */,
    total_count INTEGER NOT NULL DEFAULT 0 /* 来源行总数 */ CHECK (total_count >= 0),
    success_count INTEGER NOT NULL DEFAULT 0 /* 接受或成功关联的行数 */ CHECK (success_count >= 0),
    skip_count INTEGER NOT NULL DEFAULT 0 /* 重复或明确跳过的行数 */ CHECK (skip_count >= 0),
    issue_count INTEGER NOT NULL DEFAULT 0 /* 解析失败或冲突的行数 */ CHECK (issue_count >= 0),
    status INTEGER NOT NULL DEFAULT 0 /* 导入状态：0=PENDING，1=IMPORTED，2=PARTIAL，3=FAILED */ CHECK (status IN (0, 1, 2, 3)),
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 创建时间，UTC ISO-8601 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 最后更新时间，UTC ISO-8601 */,
    CHECK (total_count = success_count + skip_count + issue_count)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_transaction_import_file_source_sha256
    ON transaction_import_file (source_type, sha256)
    ;
