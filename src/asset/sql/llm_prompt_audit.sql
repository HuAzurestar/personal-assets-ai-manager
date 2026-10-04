PRAGMA encoding = 'UTF-8';

CREATE TABLE IF NOT EXISTS llm_prompt_audit /* One protected model attempt; append input before network I/O */ (
    id INTEGER PRIMARY KEY /* SQLite rowid */,
    run_id TEXT NOT NULL DEFAULT '' /* Scheduler diagnostic run ID, if available */,
    rule_id INTEGER CHECK (rule_id > 0) /* Optional historical tag rule ID */,
    rule_revision INTEGER CHECK (rule_revision > 0) /* Optional analysis revision */,
    ledger_id INTEGER CHECK (ledger_id > 0) /* Optional Ledger ID */,
    model_id INTEGER CHECK (model_id > 0) /* Optional model profile ID */,
    attempt INTEGER NOT NULL CHECK (attempt BETWEEN 1 AND 3) /* Retry ordinal */,
    model_name TEXT NOT NULL /* LiteLLM model name, never an API key */,
    request_json TEXT NOT NULL /* Exact outbound messages and response_format, excluding credentials */,
    response_text TEXT NOT NULL DEFAULT '' /* Provider message content; empty when unavailable */,
    response_truncated INTEGER NOT NULL DEFAULT 0 CHECK (response_truncated IN (0, 1)) /* 1 when response exceeded capture limit */,
    status TEXT NOT NULL CHECK (status IN ('STARTED', 'SUCCEEDED', 'SUGGESTED', 'INSUFFICIENT', 'REJECTED', 'ERROR')) /* External fact; legacy outcomes preserved */,
    error_code TEXT NOT NULL DEFAULT '' /* Safe adapter error code; never raw exception text */,
    source TEXT NOT NULL DEFAULT 'legacy.tag-scan' /* Registered invocation source */,
    operation_id TEXT /* Stable logical operation, independent of run ID */,
    metadata_json TEXT NOT NULL DEFAULT '{}' /* Restricted dispatch/response/recovery metadata */,
    prompt_id TEXT /* File resource identity or editable template ID */,
    prompt_fingerprint TEXT /* SHA-256 of the actual template */,
    input_tokens INTEGER /* Unknown usage is NULL */,
    output_tokens INTEGER /* Unknown usage is NULL */,
    total_tokens INTEGER /* Unknown usage is NULL */,
    estimated_cost TEXT /* Optional decimal estimate, not a settled charge */,
    created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* UTC ISO-8601 microseconds */,
    updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z', 'now')) /* UTC ISO-8601 microseconds */
);

CREATE INDEX IF NOT EXISTS ix_llm_prompt_audit_item ON llm_prompt_audit (rule_id, ledger_id);
