---
name: ledger-api
description: Change PAAM ledger projections, summaries, tags, DTOs/VOs, Services, or SQL Data Mappers.
---

# Ledger and API layer

- Read `.agents/skills/router-layer/SKILL.md` for HTTP modules, URL versions,
  Router file names, dependencies, and error translation.
- A Mapper owns SQL and returns typed VOs. A Service owns the use case and
  returns response DTOs.
- Do not add SQL to a DTO/VO, Service, Controller, serializer, or frontend module.
- The hot Economic projection is the normal list/summary source. During the
  coordinated migration it remains physically stored in `ledger_entry`.
- Ledger entry type is exactly INCOME_AND_EXPENSE, INTERNAL_TRANSFER, or
  ASSET_AND_LIABILITY. The last type classifies cash flow only; it does not
  maintain asset units, valuations, or balances. Review behavior is separate.
- `review_allocation` is the physical ternary Allocation: one row maps one
  Review, one Fact, and one LedgerEntry.
- A single Economic never mixes direction or currency. Never store an exchange
  rate or convert currencies; summaries remain per currency.
- List and summary SQL names every selected column. Sparse tags are loaded once with `WHERE ledger_id IN (...)` and assembled by ID.
- Effective Tag values belong directly to one Ledger. Tag assignment reads and
  replaces that Ledger's tag relations without resolving through Fact,
  `ledger_entry_source`, or Review. Two Ledgers that share a Fact may have
  different Tag values.
- Every active Tag View has exactly one effective Tag value on each active
  Ledger. New Ledgers and newly activated Views receive the active
  `unclassified` value until explicitly assigned.
- A direct Ledger Tag replacement runs in the serialized write transaction;
  do not add an optimistic version solely for local Tag assignment.
- Add query-count tests for list endpoints. Returned row count must not increase SQL statement count.
- Keep the current hot-path backlog and measured results in `src/doc/sql-query-refactor.md`.
