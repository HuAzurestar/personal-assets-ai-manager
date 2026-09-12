---
name: ledger-api
description: Change PAAM ledger projections, summaries, tags, Controllers, DTOs/VOs, Services, or SQL Data Mappers.
---

# Ledger and API layer

- Follow `Controller -> Service -> Data Mapper -> Model / SQLite`.
- A Mapper owns SQL and returns typed VOs. A Service owns the use case and returns response DTOs. A Controller owns HTTP only.
- Do not add SQL to a DTO/VO, Service, Controller, serializer, or frontend module.
- The hot `ledger_entry` projection is the normal list/summary source. Raw evidence and history are loaded only for a bounded detail request.
- One `ledger_entry` may map to several facts/reviews through `ledger_entry_source`; each `(source_kind, source_id)` maps to exactly one ledger entry.
- `ledger_type` defines how in/out values are interpreted. `allocation_status` is the single publication state.
- Cross-currency in/out values are not subtracted or silently converted. Main-currency totals exclude unvalued foreign-currency legs and report them separately.
- List and summary SQL names every selected column. Sparse tags are loaded once with `WHERE ledger_id IN (...)` and assembled by ID.
- Add query-count tests for list endpoints. Returned row count must not increase SQL statement count.
- Keep the current hot-path backlog and measured results in `docs/sql-query-refactor.md`.
