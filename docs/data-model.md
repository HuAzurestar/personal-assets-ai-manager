# PAAM target data model

Status: target architecture for incremental migration from PIRC-9 commit `887606d`. Existing public APIs remain compatible until shadow-read verification is complete.

## Multi-source intake support tables

All fixed support tables also contain the common `id`, `created_time`, and
`updated_time` columns and use implicit IDs rather than SQL foreign keys.

### `accounts` — stable payment account dictionary

| Column | Type | Rule / meaning |
| --- | --- | --- |
| `identity` | VARCHAR | Unique normalized account identity |
| `provider` | VARCHAR | Bank or wallet enum |
| `display_name` | VARCHAR | Short UI name |
| `number` | VARCHAR | Full/masked account number, default `''` |
| `owner` | VARCHAR | Exported owner name, default `''` |

This is a small, cold enhancement dictionary. Ledger lists keep only the stable
account code; verbose account data is loaded for detail/import matching.

### `account_bindings` — sparse detected-to-stable mapping

| Column | Type | Rule / meaning |
| --- | --- | --- |
| `detected_identity` | VARCHAR | Unique source-file account identity |
| `account_id` | INTEGER | Implicit `accounts.id` |
| `basis` | VARCHAR | Why this mapping was accepted |

### `import_identities` — alternate transaction identities

| Column | Type | Rule / meaning |
| --- | --- | --- |
| `key` | CHAR(64) | Unique deterministic source identity hash |
| `bill_id` | INTEGER | Implicit canonical transaction/fact ID |

One transaction can have bank and wallet identities, so this cannot be reduced
to one `bill_fact.fact_key` without losing deduplication evidence.

### `import_previews` — cold expiring command state

| Column | Type | Rule / meaning |
| --- | --- | --- |
| `token` | VARCHAR | Unique confirmation token |
| `payload_json` | TEXT | Parsed documents; never bytes/passwords |
| `plan_json` | TEXT | Deterministic preview/version |
| `result_json` | TEXT | Idempotent result; empty before confirmation |

Expiry uses the common `created_time`; no second creation timestamp is stored.

`import_evidence` is compatibility storage from the merged branch. Its
`bill_id/import_batch_id/row_number/record_json/disposition` fields map to target
`bill_raw` plus `import_file`; it is not a second long-term Fact table. The next
write-path phase replaces it and the legacy
`import_batches/import_artifacts/ledger_origins` trio with
`bill_raw/import_file`, then retires compatibility writes after comparison.

## Common SQL contract

Every physical table contains:

| Column | Type | Rule |
| --- | --- | --- |
| `id` | INTEGER | Primary identifier |
| `created_time` | DATETIME | `NOT NULL`, SQL default current time |
| `updated_time` | DATETIME | `NOT NULL`, SQL default current time |

Relationships are implicit IDs without SQL `FOREIGN KEY` declarations. Services validate IDs in batches. Indexes are limited to primary/unique identifiers and measured hot lookup paths.

Business columns are `NOT NULL`. Optional text uses `DEFAULT ''`; unknown semantics use an explicit enum such as `UNKNOWN`. Missing required amount/time/direction never receives a fabricated value and remains an invalid raw row.

## Fact layer

### `import_file` — imported artifact

| Column | Type | Default | Meaning |
| --- | --- | --- | --- |
| `batch_code` | VARCHAR | `''` | Groups files submitted together |
| `source_type` | VARCHAR | `UNKNOWN` | Alipay, WeChat, bank, manual, etc. |
| `institution_code` | VARCHAR | `UNKNOWN` | Originating bank/platform |
| `filename` | VARCHAR | `''` | User-visible original name |
| `file_format` | VARCHAR | `UNKNOWN` | CSV/XLS/XLSX/ZIP |
| `sha256` | CHAR(64) | `''` | Whole-file fingerprint |
| `period_start` | ISO datetime text | `''` | Earliest source record time; empty means the file has no accepted dated row |
| `period_end` | ISO datetime text | `''` | Latest source record time; empty means the file has no accepted dated row |
| `total_count` | INTEGER | `0` | Source rows considered |
| `success_count` | INTEGER | `0` | Rows accepted or linked to facts |
| `skip_count` | INTEGER | `0` | Duplicate/explicitly ignored rows |
| `issue_count` | INTEGER | `0` | Invalid/conflicting rows |
| `status` | ENUM text | `PENDING` | PENDING/IMPORTED/PARTIAL/FAILED |

`total_count = success_count + skip_count + issue_count`. `(source_type, sha256)` is unique when SHA is present.

### `bill_raw` — immutable source row and parse state

| Column | Type | Default | Mutability / meaning |
| --- | --- | --- | --- |
| `bill_id` | INTEGER | `0` | Mutable implicit fact link; multiple raws may link one fact |
| `import_file_id` | INTEGER | `0` | Immutable implicit file link |
| `source_row_number` | INTEGER | `0` | Immutable source position |
| `source_reference` | VARCHAR | `''` | Provider transaction/order reference |
| `raw_payload` | TEXT | `'{}'` | Immutable original field JSON |
| `raw_hash` | CHAR(64) | `''` | Immutable canonical row fingerprint |
| `parse_status` | ENUM text | `PENDING` | PENDING/SUCCESS/DUPLICATE/SKIPPED/INVALID/CONFLICT |
| `issue_code` | VARCHAR | `''` | Stable machine-readable error |
| `issue_message` | TEXT | `''` | Human-readable detail |

`(import_file_id, source_row_number)` is unique. `bill_id` is intentionally not unique: repeated exports may provide several raw evidence rows for one fact.

### `bill_fact` — accepted normalized accounting fact

| Column | Type | Default | Mutability / meaning |
| --- | --- | --- | --- |
| `fact_key` | VARCHAR | no fabricated default | Immutable stable provider key or accepted canonical fingerprint |
| `occurred_time` | DATETIME | no fabricated default | Immutable transaction time |
| `cash_direction` | ENUM text | no fabricated default | Immutable IN/OUT |
| `amount_value` | BIGINT | no fabricated default | Immutable integer atomic amount |
| `amount_scale` | SMALLINT | `2` | Immutable decimal scale |
| `currency_code` | VARCHAR | `CNY` | Immutable currency/unit code |
| `account_code` | VARCHAR | `UNKNOWN` | Immutable normalized source account; an ACCOUNT Review may override it in the projection |
| `counterparty` | VARCHAR | `''` | Immutable normalized source counterparty |
| `summary` | TEXT | `''` | Immutable normalized source description |

Money is `amount_value / 10^amount_scale`. Different currencies are never directly summed. A new raw record that contradicts immutable fact fields becomes `CONFLICT` and enters Review.

## Review layer

### `review_case` — current review decision

| Column | Type | Default | Meaning |
| --- | --- | --- | --- |
| `review_type` | ENUM text | `UNKNOWN` | AA/LOAN_BORROW/LOAN_LEND/REFUND/TRANSFER/FX_EXCHANGE/DUPLICATE/TAG/ACCOUNT/FACT_CONFLICT |
| `status` | ENUM text | `PENDING` | PENDING/CONFIRMED/REJECTED/REVOKED |
| `allocation_status` | ENUM text | `PARTIAL` | PARTIAL/COMPLETE/CONFLICT |
| `version` | INTEGER | `1` | Optimistic concurrency version |
| `title` | VARCHAR | `''` | Short user-facing description |
| `result_json` | TEXT | `'{}'` | Small type-specific state; never contains member ID arrays |

`(id, version)` identifies the current aggregate version. Clients write with `expected_version`.

### `review_case_bill` — bills and amounts participating in a case

| Column | Type | Default | Meaning |
| --- | --- | --- | --- |
| `case_id` | INTEGER | `0` | Implicit case ID |
| `bill_id` | INTEGER | `0` | Implicit fact ID |
| `role` | ENUM text | `UNKNOWN` | AA_PAID, AA_RECEIVED, LOAN_RECEIVED, LOAN_REPAID, LOAN_LENT, LOAN_RECOVERED, REFUND_EXPENSE, REFUND_RECEIVED, TRANSFER_OUT, TRANSFER_IN, TRANSFER_FEE, DUPLICATE_MEMBER, DUPLICATE_RETAINED, DUPLICATE_EXCLUDED, etc. |
| `party` | VARCHAR | `''` | Counterparty/person for AA and loan allocations; empty for roles that do not need one |
| `amount_value` | BIGINT | `0` | Explicit allocated atomic amount; common commands default to full remaining amount before storage |
| `amount_scale` | SMALLINT | `2` | Scale used by this allocation |
| `currency_code` | VARCHAR | `CNY` | Allocation currency; must agree with its fact |

Rows order by `id`; there is no separate position column. Services batch-check that confirmed cases do not allocate more than the available fact amount.

A refund inflow owns one `REFUND` case even when it is allocated to several
expenses. Its current lines contain one full `REFUND_RECEIVED` row plus active
`REFUND_EXPENSE` allocation rows. Revoked allocations remain in
`review_history`, not in the current line set.

An `ACCOUNT` case exists only when a user explicitly corrects a transaction's
account. The original imported account remains raw evidence; the current
reviewed account is stored in the case result and its complete change chain is
stored in `review_history`.

A `TAG` case exists for a bill with tag audit activity. Confirmed selections are
the authoritative enhancement state; rule/LLM suggestions remain non-publishing
proposals. The compact case result keeps the current category/system-name map
and at most one latest proposal, while every confirm, suggestion, and undo stays
deterministically reconstructable in `review_history`.

A `FACT_CONFLICT` case points to its immutable `bill_raw` row through the compact
`bill_raw_id` result field. Pending or dismissed rows have no fabricated bill
member. A resolved conflict gains exactly one `FACT_ACCEPTED` case line after a
real `bill_fact` exists. Resolve, dismiss, and reopen actions remain an append-only
history; reopen reverses the preceding dismiss without erasing its evidence.

### `review_history` — append-only deterministic audit

| Column | Type | Default | Meaning |
| --- | --- | --- | --- |
| `case_id` | INTEGER | `0` | Implicit case ID |
| `version` | INTEGER | `1` | Resulting case version |
| `operation` | ENUM text | `CREATE` | CREATE/CONFIRM/UPDATE/REVOKE/RESTORE |
| `schema_version` | INTEGER | `1` | Snapshot contract version |
| `request_json` | TEXT | `'{}'` | Canonical submitted command |
| `before_json` | TEXT | `'{}'` | Complete canonical aggregate before change |
| `after_json` | TEXT | `'{}'` | Complete canonical aggregate after change |
| `snapshot_hash` | CHAR(64) | `''` | Integrity hash of canonical after state |
| `reverses_history_id` | INTEGER | `0` | Implicit history ID reversed by this event |
| `actor` | VARCHAR | `local-user` | Actor identity |
| `reason` | TEXT | `''` | Human reason |
| `idempotency_key` | VARCHAR | `''` | Unique non-empty command key |

`(case_id, version)` and non-empty `idempotency_key` are unique. History rows are never updated or deleted; undo appends a reversing row.

## Enhanced hot ledger

### `ledger_entry` — published atomic ledger projection

| Column | Type | Default | Meaning |
| --- | --- | --- | --- |
| `ledger_type` | ENUM text | `UNRESOLVED` | INCOME/EXPENSE/AA/LOAN_BORROW/LOAN_LEND/REFUND/TRANSFER/FX_EXCHANGE/PASS_THROUGH/UNRESOLVED |
| `allocation_status` | ENUM text | `DEFAULT` | DEFAULT/PARTIAL/COMPLETE/EXCLUDED/CONFLICT |
| `title` | VARCHAR | `''` | Compact list label |
| `start_time` | DATETIME | no fabricated default | First contributing fact time |
| `end_time` | DATETIME | no fabricated default | Last contributing fact time |
| `in_amount_value` | BIGINT | `0` | Total incoming atomic value |
| `in_amount_scale` | SMALLINT | `2` | Incoming scale |
| `in_currency_code` | VARCHAR | `CNY` | Incoming currency |
| `out_amount_value` | BIGINT | `0` | Total outgoing atomic value |
| `out_amount_scale` | SMALLINT | `2` | Outgoing scale |
| `out_currency_code` | VARCHAR | `CNY` | Outgoing currency |
| `in_account_code` | VARCHAR | `UNKNOWN` | Effective incoming account; `MULTIPLE` means detail contains several accounts |
| `out_account_code` | VARCHAR | `UNKNOWN` | Effective outgoing account; `MULTIPLE` means detail contains several accounts |
| `input_hash` | CHAR(64) | `''` | Hash of sorted fact/review inputs |
| `projection_version` | INTEGER | `1` | Projection rule version |

Only the projection service updates this table. For cross-currency entries, in/out are shown separately and never subtracted without a future valuation policy.

Projection groups facts by confirmed financial Review connectivity. A normal
fact remains one INCOME or EXPENSE entry; AA, loan, refund, transfer, and FX
cases produce one entry for their connected facts. A confirmed duplicate case
counts only its `DUPLICATE_RETAINED` fact while keeping excluded facts available
for lineage. Cash legs always come from full immutable fact amounts, not from
display labels or mutable legacy totals. Different scales of the same currency
are normalized exactly; multiple currencies in the same direction block the
projection instead of being silently combined. A missing zero leg uses the real
unit/scale of the opposite leg for display and does not create a financial value.

`input_hash` covers sorted immutable fact fields plus every confirmed Review
version, result, and normalized line in the component. Target drift is reported;
shadow migration never overwrites a mismatching entry.

Account projection follows the same deterministic rule. The immutable Fact
keeps the imported account code; a confirmed ACCOUNT Review supplies the
effective code. Each cash direction projects its single effective account,
`MULTIPLE` when several accounts contribute, or `UNKNOWN` when source evidence
does not identify one. Import institution and file provenance remain detail-only.

### `ledger_entry_source` — deterministic projection lineage

| Column | Type | Default | Meaning |
| --- | --- | --- | --- |
| `ledger_id` | INTEGER | `0` | Implicit ledger entry ID |
| `source_kind` | ENUM text | `BILL_FACT` | BILL_FACT or REVIEW_CASE |
| `source_id` | INTEGER | `0` | Corresponding implicit ID |

`(source_kind, source_id)` is unique. One ledger entry may have many sources; every source resolves to exactly one published projection.

Every published fact contributes exactly one `BILL_FACT` source row. Every
confirmed Review that participates in a component contributes exactly one
`REVIEW_CASE` source row. Source-row IDs are deterministic, so shadow backfill
is idempotent and a detail request can recover both immutable facts and the
complete Review trail without querying unrelated entries.

## Tags

### `tag_view`

| Column | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | VARCHAR | `''` | Display name |
| `system_name` | VARCHAR | `''` | Stable code |
| `status` | ENUM text | `ACTIVE` | ACTIVE/ARCHIVED |

### `tag`

| Column | Type | Default | Meaning |
| --- | --- | --- | --- |
| `view_id` | INTEGER | `0` | Implicit tag view ID |
| `name` | VARCHAR | `''` | Display value |
| `system_name` | VARCHAR | `''` | Stable value code |
| `status` | ENUM text | `ACTIVE` | ACTIVE/ARCHIVED |

Target tag views and values preserve their legacy IDs so Review tag system names
can be resolved without a translation table. Legacy tag values do not have their
own timestamps; their target `created_time` and `updated_time` therefore use the
real creation time of the owning tag view. An archived view makes all its values
effectively archived.

### `ledger_entry_tag`

| Column | Type | Default | Meaning |
| --- | --- | --- | --- |
| `ledger_id` | INTEGER | `0` | Implicit ledger entry ID |
| `tag_id` | INTEGER | `0` | Implicit tag ID |

`(ledger_id, tag_id)` is unique. Page tags load in one bounded ID batch. Do not introduce Elasticsearch for exact tag filtering at the current scale.

Each active tag view contributes exactly one effective tag to a ledger entry.
The active `unclassified` value is used unless a confirmed TAG Review supplies
an authoritative value. All facts combined into one ledger entry must agree on
that value; conflicting confirmed values are a migration blocker rather than a
last-write-wins choice. Pending suggestions never affect the hot projection.

## Hot summary contract

Dashboard summaries read only `ledger_entry` and use integer arithmetic. The
Mapper performs one count query and one grouped-leg query, independent of page
or table size; raw evidence and Review history are not loaded.

- INCOME and EXPENSE contribute their matching cash legs.
- REFUND reduces actual expense while remaining visible as refund activity.
- Same-currency AA and TRANSFER entries recognize only their in/out difference
  as actual income or expense (for example, a transfer fee).
- Loan legs and cross-currency exchange legs remain separately visible
  activities; they do not become income/expense without a valuation policy.
- Values with different scales in the same currency are normalized exactly.
  Different currencies are never added together.

## Read-model contract

The target ledger list reads only explicit `ledger_entry` columns plus one
batched tag query for the returned ledger IDs. Count, page, and tag assembly are
therefore fixed at three SELECT statements for 10 or 100 rows. Raw payloads,
import metadata, Review lines, and history are not list fields.

A single-entry detail request follows `ledger_entry_source` to its immutable
facts and confirmed projection Reviews. It additionally finds pending/rejected
Reviews related to those facts and marks them `is_projection_source = false`,
so pending work is visible without being confused with published accounting
state. Raw rows, import-file metadata, Review lines, and append-only history are
then loaded in bounded ID batches; no query runs inside an entity loop.

## Legacy 23-table disposition

| Existing table | Target action |
| --- | --- |
| `bills` | Backfill `bill_fact`; publish `ledger_entry`; retire after shadow verification |
| `tags` | Dead compatibility table; delete after migration validation |
| `tag_views` | Migrate/rename to `tag_view` |
| `view_tags` | Migrate/rename to `tag` |
| `bill_view_tags` | Dead compatibility table; delete |
| `tag_change_logs` | Migrate required history to `review_history`; retire |
| `bill_tags` | Dead compatibility table; delete |
| `import_batches` | Merge into `import_file` |
| `import_artifacts` | Merge into `import_file` |
| `ledger_origins` | Migrate to `bill_raw` and its file link |
| `import_row_issues` | Fold current problem state into `bill_raw` |
| `import_issue_actions` | Migrate resolve/dismiss/reopen history to FACT_CONFLICT Review; retire |
| `tag_audits` | Current effect becomes Review/projection; history migrates; retire |
| `review_candidates` | Migrate to pending `review_case` rows |
| `candidate_action_logs` | Migrate to `review_history` |
| `account_revisions` | Migrate to ACCOUNT Review and history |
| `refund_allocations` | Migrate to REFUND case bills |
| `refund_allocation_audits` | Migrate to `review_history` |
| `refund_designations` | Current effect becomes REFUND ledger type |
| `refund_nature_audits` | Migrate to `review_history` |
| `review_matters` | Migrate to `review_case` |
| `review_matter_revisions` | Current state becomes case/bills; history migrates |
| `asset_snapshots` | Keep outside the ledger core in the asset module, or archive/drop only after usage and row-count verification |

No legacy table is dropped in the first deployment. The migration sequence is: create target tables, backfill, dual/shadow read, compare IDs/amounts/hashes, switch reads, stop legacy writes, observe, then drop explicitly approved tables.
