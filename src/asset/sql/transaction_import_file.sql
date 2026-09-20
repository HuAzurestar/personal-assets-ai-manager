PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS transaction_import_file /* 一次交易数据导入的来源文件 */ (
    id INTEGER PRIMARY KEY /* 隐式主键，由 SQLite rowid 自动生成 */,
    batch_code TEXT NOT NULL DEFAULT '' /* 同一次提交的批次标识 */,
    source_type INTEGER NOT NULL DEFAULT 0 /* 来源：0=UNKNOWN，1=MANUAL，101=ALIPAY，102=WECHAT，201=CCB_BANK，202=ABC_BANK，203=CMB_BANK */,
    filename TEXT NOT NULL /* 用户看到的原始文件名 */,
    file_format INTEGER NOT NULL DEFAULT 0 /* 实际解析内容格式：0=UNKNOWN，1=CSV，2=XLS，3=XLSX，4=PDF */,
    sha256 TEXT NOT NULL /* 整个来源文件的 SHA-256 指纹 */,
    period_start TEXT NOT NULL DEFAULT '' /* 文件内最早有效交易时间，ISO-8601；未知时为空串 */,
    period_end TEXT NOT NULL DEFAULT '' /* 文件内最晚有效交易时间，ISO-8601；未知时为空串 */,
    total_count INTEGER NOT NULL DEFAULT 0 /* 来源行总数 */,
    success_count INTEGER NOT NULL DEFAULT 0 /* 接受或成功关联的行数 */,
    skip_count INTEGER NOT NULL DEFAULT 0 /* 重复或明确跳过的行数 */,
    issue_count INTEGER NOT NULL DEFAULT 0 /* 解析失败或冲突的行数 */,
    status INTEGER NOT NULL DEFAULT 0 /* 导入状态：0=PENDING，1=IMPORTED，2=PARTIAL，3=FAILED */,
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 创建时间，UTC ISO-8601 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')) /* 最后更新时间，UTC ISO-8601 */,
    UNIQUE (sha256)
);
