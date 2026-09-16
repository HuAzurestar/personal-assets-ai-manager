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
- Economic Type is exactly TRANSACTION, ACCOUNT_TRANSFER, or CLAIM. AA, loan,
  refund, and FX are Review behavior codes, not Economic Types.
- `review_case_bill` is the physical ternary Allocation during migration:
  one row maps one Review, one Fact, and one Economic.
- Every Economic/Ledger Entry has exactly one Allocation and therefore points
  to exactly one Fact. A Fact may be split across several Ledger Entries. Do
  not combine several Facts into one Ledger Entry.
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
- A direct Ledger Tag replacement uses optimistic `projection_version`
  concurrency. Repeating the already-effective complete state is an idempotent
  success and does not advance the version again.
- Add query-count tests for list endpoints. Returned row count must not increase SQL statement count.
- Keep the current hot-path backlog and measured results in `src/doc/sql-query-refactor.md`.
