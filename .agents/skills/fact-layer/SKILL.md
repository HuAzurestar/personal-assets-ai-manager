---
name: fact-layer
description: Change PAAM import files, raw rows, normalized bill facts, parsing, deduplication, or source evidence.
---

# Fact layer

Use `docs/data-model.md` as the target schema contract.

- `import_file` represents one imported artifact and owns source, institution, SHA-256, covered period, and row counts.
- `bill_raw` represents one immutable source row. `(import_file_id, source_row_number)` is unique. Multiple raw rows may point to the same `bill_fact`.
- `bill_fact` contains only stable normalized accounting facts. Optional export fields remain raw evidence.
- A richer repeat export adds another raw row; it does not overwrite the first raw row or accepted fact.
- Core conflicts create `CONFLICT` raw state and require Review. Never silently replace amount, direction, time, or currency.
- Keep row parsing set-oriented. Batch-check source references/fingerprints and batch-write accepted rows.
- Missing required accounting fields do not receive fabricated defaults and do not produce a fact until resolved.
