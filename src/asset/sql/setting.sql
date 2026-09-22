PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS setting /* 应用设置根对象；本期只允许 id=1 */ (
    id INTEGER PRIMARY KEY CHECK (id = 1) /* 固定根对象 ID */,
    value_json TEXT NOT NULL /* schema_version=1 的设置 JSON；密钥不得写入 */,
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* 创建时间，UTC ISO-8601 微秒格式 */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* 最后更新时间，UTC ISO-8601 微秒格式 */
);
