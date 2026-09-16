# PAAM engineering rules

PAAM uses a layered modular monolith. Keep the trusted ledger transactionally consistent; do not introduce another deployed service or data store without an accepted architecture decision.

## Runtime profile

- The default deployment is one local machine, one application process, one
  SQLite database, and low write concurrency.
- Serialize writes. Every mutating Service owns one short transaction and, on
  SQLite, acquires the write slot before reading data that controls the write.
- Batch-load and validate all affected rows before mutation. Do not perform
  network calls, file parsing, or user interaction while a write transaction is open.
- Idempotency protects command retries. Do not add optimistic version fields
  solely for hypothetical multi-user or distributed writers; revisit that
  decision only when the deployment profile changes.

## Required flow

`Router -> Service -> Data Mapper -> Entity / SQLite`

- Routers own HTTP parsing, response codes, and DTO validation only.
- Services own use cases, business rules, transactions, idempotency, and projection updates.
- Data Mappers own explicit-column SQL, batch loading, and row-to-VO assembly.
- Entities declare storage only, one file per table. DTOs and VOs never execute SQL.
- Do not add a Repository layer unless it has a documented responsibility that a Mapper does not already provide.
- Frontend code calls versioned APIs and never imports backend entities.
- Custom file and directory names use singular nouns. Preserve tool-required
  names, product names, upstream license names, and generated evidence names.
- Code lives in `src/backend` and `src/frontend`; assets in `src/asset`.
  Supporting files live in `src/doc`, `src/script`, `src/test`, and `src/report`.
- Parsers adapt file inputs; Services retain use-case orchestration. Do not
  reorganize existing Mapper/Service responsibilities during path-only changes.

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
  confirmed DEFAULT Review and an equal INCOME_AND_EXPENSE entry in the same transaction.
- Ledger projections are rebuildable and may only be written by the economic review service.
- Historical payloads and raw evidence are detail-only data and must not be loaded by list/summary queries.
- A Review change, its ternary allocations, DEFAULT residuals, and every affected
  economic projection update commit atomically.

Read the relevant module skill before changing that module:

- `.agents/skills/fact-layer/SKILL.md`
- `.agents/skills/review-layer/SKILL.md`
- `.agents/skills/ledger-api/SKILL.md`

The authoritative 10-table dictionary and layer boundaries are in `src/doc/data-model.md`.
