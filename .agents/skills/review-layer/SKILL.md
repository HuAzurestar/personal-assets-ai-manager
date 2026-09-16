---
name: review-layer
description: Change PAAM review cases, allocations, duplicate/refund/AA/loan/transfer decisions, undo, or audit history.
---

# Review layer

- Use one `review_case` lifecycle with an enum type; do not create one table per Review type.
- Tag definition and Ledger Tag assignment are not Review use cases. They do
  not create `TAG` Review cases, allocation rows, or Review history; Tag
  auditing requires a dedicated Tag/Ledger audit design if it is needed later.
- One flow case owns several allocation rows. Every allocation carries
  `review_case_id`, `transaction_fact_id`, `ledger_entry_id`, and one explicit money value; never embed
  member IDs in JSON.
- Every Economic/Ledger Entry is backed by exactly one allocation to exactly
  one Fact. One Fact may be divided into several Ledger Entries; several Facts
  must never be merged into one Ledger Entry.
- Fact, allocation, and Economic values have equal direction and currency.
- The effective allocations of every accepted Fact sum exactly to the Fact
  amount. A manual Review may consume part of the DEFAULT allocation; its
  residual remains a confirmed DEFAULT INCOME_AND_EXPENSE entry.
- Review policies execute in the backend service. The frontend never calculates or confirms accounting effects itself.
- The default runtime is single-machine, serialized-write, and low-concurrency.
  Review commands use one short write transaction rather than optimistic versions.
- A persisted Review is either CONFIRMED or REVOKED. Drafts stay in the client;
  creating a Review publishes its Ledger entries and allocations atomically.
- Every successful change appends `review_revision` with operation CREATE,
  UPDATE, REVOKE, or RESTORE plus canonical before/after snapshots, request,
  actor, reason, and idempotency key.
- Undo appends REVOKE or RESTORE; it never mutates or deletes old history.
- Revoking a Review retains its Ledger entries and allocations; confirmed-only
  read predicates decide whether those rows are effective.
- Batch-load every affected fact and active allocation before validation. Do not query one bill at a time.
- Allocation read queries support filtering by each member of the ternary
  relation: `review_id`, `fact_id`, and `economic_id`. Review detail derives
  related Facts and Ledgers through Allocation; it must not imply a direct
  Review-to-Fact or Review-to-Ledger relationship.
- Review detail relationships are read-only. Creating a Review is a separate
  explicit page action, not an edit hidden inside a relation section.
- Review state, replaced DEFAULT allocations, residual DEFAULT coverage, and all
  affected Economic projections update in one transaction.
