# LLM prompt audit (SQLite)

PIRC-40 routes scheduled analysis, connection checks and the fictional monthly
summary through the public `LlmClient.generate()` boundary. One explicit attempt
has one `llm_prompt_audit` row; there are no SDK retries or fallback routes.
The protected DTO and read-only prompt template are fixed before the call.
Business validation and submission are separate from the provider audit.
The legacy adapter remains only for compatibility/fixture callers; it is not the
runtime composition root.

Read-only `/paam/system/v1/llm/prompt/*` endpoints expose deployed templates,
references and server-owned fictional previews without generation. Call list and
statistics endpoints select summary/numeric columns only. Sensitive call detail
is disabled by default; enabling it requires a deployment-only authorization
token of at least 32 characters in `PAAM_LLM_AUDIT_DETAIL_TOKEN`, supplied through
`X-PAAM-Audit-Token`, never a URL/query parameter. This gate does not authenticate
the optional legacy SQLite browser below; keep that browser independently local.

The local PR preview Docker image mounts the existing `sqlite-web` browser at
`http://127.0.0.1:18779/sql/` on the same application port. It is enabled by
`PAAM_SQL_WEB_ENABLED=1` in `src/script/Dockerfile.preview`; other deployment
methods leave it disabled unless they explicitly set that variable. The mount
opens only the configured SQLite file in SQLite read-only mode. Bind the Docker
port to `127.0.0.1`, never a public interface: the browser has no authentication
and can display or download the full financial database.

The table is created in the existing PAAM SQLite database at normal startup.
The database location comes from `PAAM_DATABASE_URL`; by default it is
`PAAM_DATA_DIR/personal-assets-ai-manager.db`. The PIRC-40 migration atomically
rebuilds only this table, preserving every old column/row/id. Unknown old shapes
are refused, failed migrations roll back, and missing usage/cost stays NULL.

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
saved. `metadata_json.dispatch_state` distinguishes NOT_SENT,
MAY_HAVE_EXECUTED and RESPONSE_RECEIVED. MAY_HAVE_EXECUTED or an unfinished call
blocks automatic tag re-dispatch for that item, even after a new process or rule
revision. A post-audit failure must repair the original row, not generate again.
`ERROR` stores only a safe code; no SDK exception body is retained. `SUCCEEDED`
describes provider success, not a valid or committed suggestion. Legacy REJECTED,
SUGGESTED and INSUFFICIENT rows are preserved as RESPONSE_RECEIVED, including
repair of databases upgraded by the initial PIRC-40 migration. Legacy STARTED
and ERROR rows remain uncertain. Completed legacy rows do not block analysis
after a semantic rule change. Recoverable SUCCEEDED rows must carry the
`envelope_validated=true` marker: choice, role, refusal, tool calls and finish
reason are checked before projecting and persisting the response. Older
SUCCEEDED rows without this marker cannot be safely restored or resent.
Cancellation before outbound admission prevents sending and finishes the audit
as ERROR/CANCELLED with NOT_SENT; an admitted worker is drained to completion.
Responses over 256 KiB are
truncated and marked with `response_truncated=1`.

`request_json` contains the exact system/user messages and response format sent
to LiteLLM, but not the API key, HTTP headers, proxy URL, or SDK exception.
Inputs were privacy-filtered before this boundary; raw model responses can
still contain sensitive text. Treat the database and its backups as sensitive,
restrict filesystem access, and do not attach them to bug reports. If an audit
write fails, the scan stops without advancing its ledger cursor, rather than
making an unrecorded provider call. Known untruncated success without submission
can be revalidated from the saved response; cancelled/stale responses are never
committed as a newer configuration's suggestion. The rule's bounded
`last_analysis_json` records PASSED/REJECTED plus COMMITTED/STALE/CANCELLED/FAILED,
and committed suggestion rows retain `call_id`. Neither business record copies
model messages or response content.
