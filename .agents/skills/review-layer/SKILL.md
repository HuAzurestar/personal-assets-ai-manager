---
name: review-layer
description: Change PAAM review cases, allocations, duplicate/refund/AA/loan/transfer decisions, undo, or audit history.
---

# Review layer

- Use one `review_case` lifecycle with an enum type; do not create one table per Review type.
- One case owns several `review_case_bill` rows. Repeated members and allocated amounts are columns, never ID lists embedded in JSON.
- Common operations default to the bill's full remaining amount, but the stored amount is always explicit so partial allocation remains auditable.
- Review policies execute in the backend service. The frontend never calculates or confirms accounting effects itself.
- `review_case.version` provides optimistic concurrency. The command supplies `expected_version`.
- Every successful change appends `review_history` with canonical before/after aggregate snapshots, request, actor, reason, schema version, and idempotency key.
- Undo appends a new reversing event; it never mutates or deletes old history.
- Batch-load every affected fact and active allocation before validation. Do not query one bill at a time.
- Review state and all affected ledger projections update in one transaction.
