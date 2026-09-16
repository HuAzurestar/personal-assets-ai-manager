---
name: review-layer
description: Change PAAM review cases, allocations, duplicate/refund/AA/loan/transfer decisions, undo, or audit history.
---

# Review layer

- Use one `review_case` lifecycle with an enum type; do not create one table per Review type.
- One flow case owns several allocation rows. Every allocation carries
  `case_id`, `bill_id`, `economic_id`, and one explicit money value; never embed
  member IDs in JSON.
- Fact, allocation, and Economic values have equal direction and currency.
- The effective allocations of every accepted Fact sum exactly to the Fact
  amount. A manual Review may consume part of the DEFAULT allocation; its
  residual remains a confirmed DEFAULT INCOME_AND_EXPENSE entry.
- Review policies execute in the backend service. The frontend never calculates or confirms accounting effects itself.
- The default runtime is single-machine, serialized-write, and low-concurrency.
  Review commands use one short write transaction rather than optimistic versions.
- A persisted Review is either CONFIRMED or REVOKED. Drafts stay in the client;
  creating a Review publishes its Ledger entries and allocations atomically.
- Every successful change appends `review_revision` with canonical before/after
  aggregate snapshots, request, actor, reason, schema version, and idempotency key.
- Undo appends a new reversing event; it never mutates or deletes old history.
- Revoking a Review retains its Ledger entries and allocations; confirmed-only
  read predicates decide whether those rows are effective.
- Batch-load every affected fact and active allocation before validation. Do not query one bill at a time.
- Review state, replaced DEFAULT allocations, residual DEFAULT coverage, and all
  affected Economic projections update in one transaction.
