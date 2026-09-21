---
name: write-concurrency
description: Change PAAM write transactions, optimistic locking, updated_time tokens, retry safety, or expiring command state.
---

# Write concurrency and time tokens

- PAAM runs as one local application process over SQLite with low write
  concurrency. Keep each mutation in one short Service-owned transaction.
- On SQLite, acquire `BEGIN IMMEDIATE` before reading any state that controls a
  write. Network calls, file parsing, and user interaction stay outside the
  transaction.
- Use the existing `updated_time` as the lightweight optimistic-lock token when
  a command must reject stale UI or API state. Do not add a separate version
  column for this purpose.
- A successful mutation response must return the exact `updated_time` value
  persisted in SQLite. A caller must be able to use that response directly as
  the next command's expected token without an intervening reload.
- Persist UTC timestamps as ISO-8601 text with exactly six fractional digits
  and a `Z` suffix. Application-generated microseconds are authoritative and
  must never be truncated to milliseconds by a type adapter, DTO, or Mapper.
- When the clock has not advanced, advance a replacement token by exactly one
  microsecond beyond the previous persisted value. Never use a coarser
  millisecond increment merely to hide precision loss.
- SQLite's built-in clock may provide only millisecond source resolution.
  Schema defaults still emit the shared six-digit format by zero-filling lower
  digits, but application-owned writes must supply `utc_now()` so available
  microseconds are preserved.
- When changing timestamp precision, migrate existing text values too. Mixed
  `.sssZ` and `.ssssssZ` storage breaks lexical ordering and optimistic-lock
  equality even though both parse to equivalent datetimes.
- Test optimistic locking with three assertions: a current token succeeds, the
  returned token can immediately drive the next write, and a genuinely stale
  token fails without changing data.
- Import preview timeout defaults to 30 minutes.
  `PAAM_IMPORT_PREVIEW_TIMEOUT_MINUTES` may extend it for special workloads.
  Timeout changes a leftover PENDING file to FAILED as an advisory lifecycle
  state; it must not by itself block a still-resident preview from revision or
  confirmation, delete source evidence, or fabricate a Fact.
- Reconcile preview lifecycle on process startup and with the named background
  sweep interval. Never depend on another upload arriving to clean stale rows.
