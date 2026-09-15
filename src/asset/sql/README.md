# Target SQLite schema

This directory is the reviewed target-schema baseline. Each `.sql` file owns one
physical table and can be executed independently; relationships are logical IDs
validated in batches by Services rather than SQLite foreign keys.

Schema conventions:

- SQLite stores database text as UTF-8 and every table/column has an adjacent SQL comment.
- Every table has `id`, `created_time`, and `updated_time`.
- Required business values use `NOT NULL` without a fabricated default. Optional
  text uses `NOT NULL DEFAULT ''`; optional JSON alone may use `NULL`.
- Integer enum comments list every persisted value. `DEFAULT 0` is used only when
  zero is a valid domain state, never to bypass a required relationship or amount.
- Times are ISO-8601 text. Generated audit timestamps use UTC with a `Z` suffix;
  source occurrence times must be normalized before persistence.

These files describe the destination schema. Runtime entities and migrations move
to it in separate, green commits so an intermediate commit never silently changes
the live database contract.
