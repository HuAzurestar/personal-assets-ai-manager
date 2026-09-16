---
name: fact-layer
description: Change PAAM import files, raw rows, normalized bill facts, parsing, deduplication, or source evidence.
---

# Fact layer

Use `src/doc/data-model.md` as the target schema contract.

- `import_file` represents one imported artifact and owns source, institution, SHA-256, covered period, and row counts.
- `bill_raw` represents one immutable source row. `(import_file_id, source_row_number)` is unique. Multiple raw rows may point to the same `bill_fact`.
- `bill_fact` contains only stable normalized accounting facts. Optional export fields remain raw evidence.
- A richer repeat export adds another raw row; it does not overwrite the first raw row or accepted fact.
- Core conflicts create an `INVALID` raw row with `issue_code=FACT_CONFLICT` and require Review. Never silently replace amount, direction, time, or currency.
- Keep row parsing set-oriented. Batch-check source references/fingerprints and batch-write accepted rows.
- A preview first derives the current upload's identity keys, references, and
  date bounds. Query only matching raw evidence, imported files, and candidate
  facts; never load historical tables in full.
- Confirmation preloads every referenced ID set and performs grouped flushes.
  Do not call `get()`, `select()`, or `flush()` once per parsed row.
- Alternate bank/wallet exports may share one fact. Persist each source row in
  `bill_raw`, link it by `bill_id`, and keep only the accepted canonical identity
  in `bill_fact.fact_key`; do not add a parallel identity/evidence table.
- Import previews are bounded, expiring application command state rather than a
  ledger table. Never read previews, raw payloads, or verbose account details in
  ledger list/summary paths.
- Multi-source import writes `import_file`, `bill_raw`, and `bill_fact` in the
  Fact layer, then creates exact DEFAULT Review/TRANSACTION/Allocation coverage
  before the same transaction commits.
- Missing required accounting fields do not receive fabricated defaults and do not produce a fact until resolved.
- Transaction Fact and Import File Details lists are PO inspections, not import
  workflow or Review candidate lists. Their queries run server-side with the
  shared search/filter/sorter/page contract.
- Transaction Fact detail may read its Import File evidence, Allocations,
  Reviews, and Ledgers as relationships. Those relationships are read-only and
  must be reached through persisted identifiers rather than inferred joins.
- Import File detail may expose its Transaction Facts as a related subtable.
  Raw source payload JSON is evidence for an individual imported row, not a
  separate top-level Details object and not list data.
