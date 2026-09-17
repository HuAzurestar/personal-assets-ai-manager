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
- Every persisted and public money value is an integer `amount` accompanied by
  `currency_code`. Never add `amount_scale`, `amount_value`, a nested money
  wrapper, or a floating-point amount to SQL, Entities, VOs, DTOs, or responses.
- `currency_code` identifies both the currency and its quantum. A base code
  uses its registered default (`CNY` means `0.01 CNY`); an explicit suffix
  selects another supported quantum (`CNY_4` means `0.0001 CNY`). Currency-unit
  parsing, formatting, and validation live in one shared component.
- Arithmetic and summaries group by the exact `currency_code`. Never add,
  compare coverage, or net `CNY` with `CNY_4` without an explicit, exact
  conversion chosen by the use case.
- The hot Economic projection is the normal list/summary source. During the
  coordinated migration it remains physically stored in `ledger_entry`.
- Economic Type is exactly TRANSACTION, ACCOUNT_TRANSFER, or CLAIM. AA, loan,
  refund, and FX are Review behavior codes, not Economic Types.
- The v1 API serializes Economic Type as `economic_type` with the semantic
  string values above and direction as `cash_direction` with `IN` or `OUT`.
  Integer `entry_type` and `entry_direction` values are physical projection
  details and never appear in public requests or responses.
- Public Allocation identifiers are `review_id`, `fact_id`, and `economic_id`.
  Physical names such as `case_id`, `bill_id`, `ledger_entry_id`, or
  `transaction_fact_id` remain behind the Mapper/Service boundary.
- `review_case_bill` is the physical ternary Allocation during migration:
  one row maps one Review, one Fact, and one Economic.
- Every Economic/Ledger Entry has exactly one Allocation and therefore points
  to exactly one Fact. A Fact may be split across several Ledger Entries. Do
  not combine several Facts into one Ledger Entry.
- A single Economic never mixes direction or currency unit. Never store an exchange
  rate or convert currencies; summaries remain per currency.
- List and summary SQL names every selected column. Sparse tags are loaded once with `WHERE ledger_id IN (...)` and assembled by ID.
- Effective Tag values belong directly to one Ledger. Tag assignment reads and
  replaces that Ledger's tag relations without resolving through Fact,
  `ledger_entry_source`, or Review. Two Ledgers that share a Fact may have
  different Tag values.
- Every active Tag View has exactly one effective Tag value on each active
  Ledger. New Ledgers and newly activated Views receive the active
  `unclassified` value until explicitly assigned.
- Ledger-owned Account and Tag writes use the application's short serialized
  SQLite write transaction. Do not expose `projection_version` or require an
  optimistic-lock token in these local single-user APIs. Replacing an already
  effective complete Tag state remains an idempotent success.
- Add query-count tests for list endpoints. Returned row count must not increase SQL statement count.
- The Ledger Details list represents Ledger projection rows only. Ledger detail
  may include the Allocation, Review, Transaction Fact, and Tag relationships
  needed to explain the projection. Those relationships are read-only except
  for Ledger-owned Tag assignment.
- Keep the current hot-path backlog and measured results in `src/doc/sql-query-refactor.md`.
