# LLM prompt audit (SQLite)

Automatic-tag calls have no separate prompt HTTP endpoint. `LiteLlmAdapter._analyze()`
builds the LiteLLM request, writes one `llm_prompt_audit` row, calls the provider,
then updates that row with the provider's message content and validation outcome.
Each retry is a separate row. This table has no purpose-built business API or UI.

The local PR preview Docker image mounts the existing `sqlite-web` browser at
`http://127.0.0.1:18779/sql/` on the same application port. It is enabled by
`PAAM_SQL_WEB_ENABLED=1` in `src/report/Dockerfile.pirc9`; other deployment
methods leave it disabled unless they explicitly set that variable. The mount
opens only the configured SQLite file in SQLite read-only mode. Bind the Docker
port to `127.0.0.1`, never a public interface: the browser has no authentication
and can display or download the full financial database.

The table is created in the existing PAAM SQLite database at normal startup.
The database location comes from `PAAM_DATABASE_URL`; by default it is
`PAAM_DATA_DIR/personal-assets-ai-manager.db`. Existing rows are not backfilled.

Example read-only queries against a stopped copy or SQLite snapshot:

```sql
SELECT id, created_time, run_id, rule_id, rule_revision, ledger_id,
       model_id, attempt, status, error_code, response_truncated
FROM llm_prompt_audit
WHERE rule_id = 1 AND ledger_id = 143
ORDER BY id;

SELECT json_extract(request_json, '$.messages[0].content') AS system_prompt,
       json_extract(request_json, '$.messages[1].content') AS user_input,
       json_extract(request_json, '$.response_format') AS response_format,
       response_text, status, error_code
FROM llm_prompt_audit
WHERE id = ?;
```

`STARTED` means the outbound prompt was durably saved but no final response was
saved; the call might still be running, have timed out, or have been interrupted.
`ERROR` stores only a safe error code because provider exceptions may contain
credentials or private text. `REJECTED` retains the raw provider message that
failed local validation. `SUGGESTED` and `INSUFFICIENT` retain successful raw
messages. Responses over 256 KiB are truncated and marked with
`response_truncated=1`.

`request_json` contains the exact system/user messages and response format sent
to LiteLLM, but not the API key, HTTP headers, proxy URL, or SDK exception.
Inputs were privacy-filtered before this boundary; raw model responses can
still contain sensitive text. Treat the database and its backups as sensitive,
restrict filesystem access, and do not attach them to bug reports. If an audit
write fails, the scan stops without advancing its ledger cursor, rather than
making an unrecorded provider call.
