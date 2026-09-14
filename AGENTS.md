# PAAM engineering rules

PAAM uses a layered modular monolith. Keep the trusted ledger transactionally consistent; do not introduce another deployed service or data store without an accepted architecture decision.

## Required flow

`Controller -> Service -> Data Mapper -> Model / SQLite`

- Controllers own HTTP parsing, response codes, and DTO validation only.
- Services own use cases, business rules, transactions, idempotency, and projection updates.
- Data Mappers own explicit-column SQL, batch loading, and row-to-VO assembly.
- Models declare storage only. DTOs and VOs never execute SQL.
- Do not add a Repository layer unless it has a documented responsibility that a Mapper does not already provide.
- Frontend code calls versioned APIs and never imports backend models.

## SQL rules

- Never issue SQL from a loop, DTO, response serializer, or template.
- Never use `SELECT *`; select the fields required by the use case.
- List queries must load related sparse data with bounded `IN (...)` batches.
- Prefer primary/implicit-ID lookups and a minimal set of measured hot-path indexes. Do not add speculative indexes.
- Relationships use logical IDs rather than SQL `FOREIGN KEY` constraints. Validate referenced IDs in one batch before writes.
- Every physical table has `id`, `created_time`, and `updated_time`. Business values are `NOT NULL`; use explicit `UNKNOWN` states or empty text rather than SQL NULL. Never fabricate missing financial facts with zero/default timestamps.
- Store money as an integer value plus scale and currency. Never store money in floating-point columns in the target schema.

## Data boundaries

- Fact source fields are immutable after acceptance. Raw payloads are append-only; only their processing/link status may change.
- Confirmed Review is authoritative input. Pending suggestions never change published economic values.
- Every accepted Fact has exact confirmed allocation coverage. Import creates a
  confirmed DEFAULT Review and an equal TRANSACTION Economic in the same transaction.
- Economic projections are rebuildable and may only be written by the projection service.
- Historical payloads and raw evidence are detail-only data and must not be loaded by list/summary queries.
- A Review change, its ternary allocations, DEFAULT residuals, and every affected
  economic projection update commit atomically.

Read the relevant module skill before changing that module:

- `.agents/skills/fact-layer/SKILL.md`
- `.agents/skills/review-layer/SKILL.md`
- `.agents/skills/ledger-api/SKILL.md`

The authoritative 11-table dictionary and layer boundaries are in `docs/data-model.md`.
