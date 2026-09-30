# Manual tag assignment

`PUT /paam/tag/v1/assignment/{ledger_id}` retains the complete active-View
`tag_state` and `expected_updated_time` contract. Optional `view_names` identifies
the Views explicitly assigned by this action. It may be empty for a no-op; it
must not contain duplicates or inactive/unknown Views. Values outside that scope
cannot change. A stale timestamp is rejected before any write.

Within one short write transaction, the assigned Ledger + Views' `PENDING`
requests become `CANCELLED`, and `ENABLED` requests become `REPLACED`. Manual
assignment does not change rule decision counters. Unchanged Views retain their
tag relations, timestamps, and automatic sources.

A same-value assignment with an explicit View still transfers ownership to the
user and advances the assignment timestamp when requests are superseded. A
repeat after that takeover is a no-op. The form submits changed Views plus any
checked “接管为人工” Views; saving an untouched form does not claim every View.

Legacy callers that omit `view_names` assign changed Views. If they submit an
identical complete state, that explicit assignment claims all submitted Views.
Callers that need a narrower same-value takeover should send `view_names`.

# Protected scan input

The scanned Ledger is authoritative for amount, direction, and currency. Its
Fact supplies merchant and summary text only; a split Ledger must not disclose
the original Fact's total as its own amount. BAND and EXACT use the same integer
Ledger amount and currency unit.

Merchant and summary text are sanitized before being bounded to the model DTO's
200/500-character limits. Stored Fact text is not modified. Invalid per-item
payloads are atomically counted as failed and advance the cursor so later items
can proceed; validation logs contain error types, not raw text. Synthetic fixture
errors and rule-level provider configuration failures retain their stop behavior.

These correctness fixes do not certify the complete free-text privacy policy.
Real-data scheduled scanning remains disabled; the existing explicitly enabled
synthetic-only gate remains in place.
