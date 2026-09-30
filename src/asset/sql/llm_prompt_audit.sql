PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS llm_prompt_audit /* One protected model attempt; append input before network I/O */ (
    id INTEGER PRIMARY KEY /* SQLite rowid */,
    run_id TEXT NOT NULL DEFAULT '' /* Scheduler diagnostic run ID, if available */,
    rule_id INTEGER NOT NULL CHECK (rule_id > 0) /* Auto-tag rule logical ID */,
    rule_revision INTEGER NOT NULL CHECK (rule_revision > 0) /* Rule revision at scan time */,
    ledger_id INTEGER NOT NULL CHECK (ledger_id > 0) /* Ledger entry logical ID */,
    model_id INTEGER NOT NULL CHECK (model_id > 0) /* Configured model logical ID */,
    attempt INTEGER NOT NULL CHECK (attempt BETWEEN 1 AND 3) /* Retry ordinal */,
    model_name TEXT NOT NULL /* LiteLLM model name, never an API key */,
    request_json TEXT NOT NULL /* Exact outbound messages and response_format, excluding credentials */,
    response_text TEXT NOT NULL DEFAULT '' /* Provider message content; empty when unavailable */,
    response_truncated INTEGER NOT NULL DEFAULT 0 CHECK (response_truncated IN (0, 1)) /* 1 when response exceeded capture limit */,
    status TEXT NOT NULL CHECK (status IN ('STARTED', 'SUGGESTED', 'INSUFFICIENT', 'REJECTED', 'ERROR')) /* Attempt outcome */,
    error_code TEXT NOT NULL DEFAULT '' /* Safe adapter error code; never raw exception text */,
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* UTC ISO-8601 microseconds */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* UTC ISO-8601 microseconds */
);
