# SQL query refactor checklist

This list tracks SQL hot paths separately from the target table migration. A
checked item has a query-count regression test; it does not mean the legacy
physical table has already been migrated.

## Completed

- [x] `GET /api/transactions`
  - Controller, Service, Mapper, and typed VO are separated.
  - Page IDs are selected first; bills, origins, tag dictionary, and current tag
    revisions are loaded in bounded sets.
  - The Mapper names every selected column and performs no SQL in row assembly.
  - Verified: 10 and 100 returned rows both use at most 7 SELECT statements.
- [x] `GET /api/candidates/page`
  - Candidates, all member bill IDs, current action state, bills, origins, tags,
    and current tag revisions are batch loaded.
  - `member_bills`, `bill`, and `related_bill` reuse the same in-memory bill VO;
    they no longer rebuild the same bill with new SQL.
  - Verified: 10 and 100 returned candidates both use at most 11 SELECT statements,
    including duplicate consolidation and a populated tag dictionary.
- [x] `GET /api/candidates` compatibility endpoint
  - Uses the same batch read path as candidate pagination.
  - The endpoint remains unbounded for compatibility and should not be used by
    new UI code.
- [x] Duplicate-candidate consolidation
  - Candidate membership, seed bill keys, and matching bill facts use three set
    queries instead of per-candidate/per-bill lookups.
  - Updates are sent as bulk parameter sets and skipped when the canonical group
    is already current.
  - Verified: consolidating 50 candidate groups uses exactly 3 SELECT statements.
- [x] `GET /api/bills` compatibility endpoint
  - Keeps the legacy unbounded response contract, but now reuses the batch bill
    loader instead of calling `bill_read()` for every row.
  - New UI code must still use the bounded `/api/transactions` endpoint.
- [x] `POST /api/candidates/batch`
  - Candidate rows, member facts, action versions, idempotency records, active
    Review owners, manual matters, and refund occupancy are preloaded in sets.
  - Policy validation runs in memory in request order, so overlapping decisions
    still fail atomically without querying from the item loop.
  - Candidate/fact updates and audit inserts persist in one transaction; the
    response is batch-loaded in request order after commit.
  - Verified: batches containing 1 and 99 items execute the same number of
    SELECT statements (at most 13 with a populated tag dictionary).
- [x] `GET /api/dashboard`
  - Uses a dedicated Controller, Service, typed summary VOs, and Report Mapper.
  - The Mapper selects only bill ID/time/merchant/amount plus the bounded Review
    inputs required by the current accounting basis; it never loads raw evidence
    or historical audits.
  - Issue/import/candidate/transfer counts are combined into one SQL statement.
  - Verified: summaries covering 10 and 100 facts execute the same number of
    SELECT statements (at most 9 with tag filtering).
- [x] `GET /api/ledger/drilldown`
  - Reuses the same `DashboardService` accounting basis as the summary endpoint.
  - Sources/raw fields, candidate actions, tag audits, account revisions, refund
    allocations/audits, refund nature history, and Review matters are each loaded
    with bounded ID sets rather than per transaction.
  - Raw payload and historical evidence remain exclusive to this explicit detail
    request and are never loaded by list or summary endpoints.
  - Verified: drilldowns containing 10 and 100 facts execute the same number of
    SELECT statements (at most 24 for the source/tag fixture). Optional candidate,
    refund, import-file, and matter evidence adds one bounded query per evidence
    table, never one query per returned fact.
- [x] `PUT /api/transactions/bulk-tag-state`
  - Uses a dedicated tag Controller, Service, typed command/result VOs, and
    Mapper. The Service contains validation, optimistic-version checks, merge
    rules, and idempotency; only the Mapper executes SQL.
  - Requested bills, the active tag dictionary, current audits, and idempotency
    records are loaded in bounded sets. Bill updates, audit supersession, and new
    audit rows are persisted with set-based/bulk statements in one transaction.
  - Verified: batches containing 1 and 100 bills both execute at most 5 SELECT
    statements, including an idempotency lookup.
- [x] Review matter reads and writes
  - `GET/POST/PUT /api/review-matters` and matter undo now use a dedicated
    Controller, Service, typed DTO/VO set, and Mapper. The legacy module retains
    only compatibility guards used by older refund/account/candidate commands.
  - Matter rows, all revisions, and referenced bill display fields are loaded in
    three bounded list queries. Historical snapshots remain in the explicit
    Review response and are not loaded by the ordinary ledger list.
  - Write validation loads active matter allocations, every submitted bill, and
    refund occupancy in sets before validating lines in memory. Version changes
    and the append-only revision commit in the same immediate transaction.
  - Verified: lists containing 10 and 100 matters both execute exactly 3 SELECT
    statements; creates containing 1 and 100 lines both execute at most 7 SELECT
    statements including the fully hydrated response.
- [x] Tag-view list and definition management
  - `GET/POST/PATCH /api/tag-views` and nested tag commands now use a dedicated
    Controller, Service, typed definition VOs, and Mapper.
  - The list loads every selected view and all of their tag values in two
    explicit-column queries, then groups them by `view_id` in memory.
  - Generated `view_N` and `tag_N` identifiers are chosen from one loaded set;
    identifier generation no longer issues one query per attempted suffix.
  - Existing archive-only deletion policy, protected unclassified tags, name
    uniqueness checks, and change-log writes remain covered by API tests.
  - Verified: lists containing 10 and 100 tag views both execute exactly 2
    SELECT statements.
- [x] Refund and import-issue compatibility lists
  - `GET /api/refunds` now batch-loads designated bill IDs, list-card bill VOs,
    all allocations, and all nature history. It reuses the bounded ledger bill
    Mapper and never loads raw source evidence.
  - `GET /api/import-issues` explicitly joins issue rows to their import batch,
    loads all issue actions in one `WHERE issue_id IN (...)` query, and only then
    parses raw evidence for this explicit fact-quality screen.
  - Both legacy response shapes remain unchanged. The endpoints are still
    unbounded compatibility reads; future UI pagination can be added without
    reintroducing per-row SQL.
  - Verified: import-issue lists containing 10 and 100 rows both execute exactly
    2 SELECT statements. Refund lists containing 10 and 100 rows use the same
    number of SELECT statements (at most 8 with an active tag dictionary).
- [x] Bounded cold-list DTOs and lazy detail
  - Review matters, refunds, and import issues now expose paginated summary
    endpoints. The UI requests 50 rows at a time and provides previous/next
    controls instead of downloading every cold record.
  - Summary DTOs omit revision history, raw import payloads, allocation details,
    tag/source detail, and bill notes. Those fields are loaded only through the
    bounded single-item detail endpoints after the user opens a record.
  - The Review-matter summary query extracts only the required fields and line
    count from the current JSON snapshot; it does not return the embedded lines
    or any historical revision snapshot to Python.
  - Verified: pages containing 10 and 100 rows both execute exactly 2 SELECTs for
    Review matters, 2 for import issues, and 4 for refunds.
  - The old unbounded endpoints remain temporarily available for compatibility;
    new UI code no longer calls them.
- [x] Refund command-layer separation
  - Refund allocation create/undo/audit and transaction nature get/set routes now
    use the refund Controller, Service, typed DTO/VO set, and Mapper. `main.py` no
    longer owns these five endpoints or their SQL.
  - The Service owns transaction boundaries, idempotency, amount limits, Review
    conflicts, nature transitions, and audit construction. The Mapper owns all
    explicit-column reads and writes.
  - Allocation validation loads both bills in one `IN (...)` query and all
    relevant confirmed allocations in one set query. It no longer performs one
    bill lookup per role or separate refund/expense allocation queries.
  - Verified: creating an allocation with 10 or 100 existing allocations executes
    exactly 5 SELECT statements. Existing concurrency, undo, idempotency, audit,
    and nature regression tests remain unchanged.
- [x] Import-issue commands and bounded candidate generation
  - Import-issue resolve/dismiss/reopen routes now use the import Controller,
    Service, typed DTO/VO set, and Mapper. Their transaction and raw-evidence
    preservation rules no longer live in `main.py`.
  - Correcting an issue validates the issue and import batch in one query, writes
    the accepted bill/origin/action in one transaction, and loads its response
    through the shared ledger Mapper.
  - Manual bill creation, file import, and issue correction now share one
    candidate-suggestion Service. It loads target bills, five-minute matches,
    duplicate components, and pending duplicate groups in bounded sets, applies
    matching rules in memory, then writes candidates together.
  - Verified: candidate generation for 1 and 100 newly accepted facts executes
    exactly 4 SELECT statements. Import rollback, duplicate grouping, transfer
    evidence, issue correction, dismissal, reopening, and history tests pass.
- [x] Measured hot-path indexes
  - `EXPLAIN QUERY PLAN` was captured before adding indexes. Ledger date paging
    scanned `bills` and built a temporary order B-tree; candidate time matching,
    candidate actions, refund allocations, and current tag audits also scanned
    their full tables.
  - Added one general fact-time index and four logical-ID indexes:
    `(bills.occurred_at, bills.id)`, `(candidate_id, id)`,
    `(refund_bill_id, status, id)`, `(expense_bill_id, status, id)`, and
    `(bill_id, superseded, id)` for current tag audits.
  - Candidate time matching now uses an indexable five-minute range instead of
    wrapping the stored time column in `julianday()`.
  - Verified plans use range search for ledger paging and candidate matching,
    covering indexes for candidate/tag revision grouping, and SQLite multi-index
    OR for refund allocation checks. Ledger paging no longer reports a temporary
    B-tree for ordering.
  - A query-plan regression test names every intended index. No index was added
    for cold raw/history detail tables.
- [x] Test database isolation
  - The baseline health/bill API test now overrides both the application database
    and request session with a temporary SQLite file. Full regression runs no
    longer append fixture bills to the workspace ledger.
  - Existing workspace rows were not deleted automatically because accepted fact
    cleanup requires an explicit, separately reviewed decision.
- [x] P3a target schema and fact-layer shadow backfill
  - Added the 11 target tables from `docs/data-model.md`. Every table has the
    common ID/timestamps, business fields are non-null, and all relationships
    remain implicit IDs without SQL foreign-key declarations.
  - `import_batches` plus their single artifact become `import_file`; accepted
    bills become integer/scale `bill_fact` rows; origin and issue rows sharing
    `(import_file_id, source_row_number)` merge into one immutable `bill_raw`.
  - The opt-in backfill only inserts missing target rows. It never updates a
    target fact, alters legacy rows, switches an API read, or deletes a table.
    A second execution is idempotent; drift is reported by row IDs and SHA-256
    table digests instead of being silently repaired.
  - Legacy bills, batches, artifacts, origins, and issues are each loaded once;
    target tables are each loaded once before and after insertion. Verified: 10
    and 100 accepted facts both execute exactly 11 SELECT statements.
  - Ambiguous legacy state (multiple artifacts for one batch, conflicting raw
    payloads, broken implicit IDs, count disagreement, or zero-direction facts)
    is returned as a blocker and prevents a passing shadow report.
- [x] P3b1 manual-matter Review shadow backfill
  - Added `party` to `review_case_bill`; AA and loan history cannot be restored
    deterministically without keeping the person/counterparty on each allocated
    line. The field is non-null and does not require another table.
  - Current `review_matters` snapshots become namespaced `review_case` rows and
    explicit `review_case_bill` allocations. Member bill IDs never leak into the
    small `result_json`; they remain normalized on line rows.
  - Every immutable matter revision becomes a canonical `review_history`
    before/after snapshot with operation, request, actor, reason, version,
    idempotency identity, reversal pointer, and SHA-256 integrity hash.
  - Migration requires the referenced `bill_fact` rows, preserves partial versus
    complete allocation, refuses mixed/unknown semantics, and reports drift
    without overwriting target Review state.
  - Verified: 10 and 100 matters both execute exactly 9 SELECT statements. The
    migration is idempotent and does not mutate matter or revision source rows.
- [x] P3b2a candidate Review shadow backfill
  - Candidate creation becomes history version 1; every decision log appends a
    deterministic version. Confirm, update, and undo map to explicit operations,
    and undo points to the exact history row it reverses.
  - Pending, deferred, and evidence-limited suggestions remain `PENDING` and
    cannot affect projection totals. Only balanced personal transfers and valid
    retained/excluded duplicate decisions migrate as `CONFIRMED` cases.
  - Candidate member IDs and full integer amounts live in `review_case_bill`.
    Current `result_json` contains only compact decision metadata; raw legacy
    before/after states remain detail-only inside immutable history snapshots.
  - Broken action chains, a current row that disagrees with its latest action,
    invalid undo pointers, missing facts, unbalanced transfers, and inconsistent
    duplicate members are reported rather than repaired or guessed.
  - Verified: 10 and 100 candidates both execute exactly 9 SELECT statements;
    pending, confirmed-transfer, confirm-then-undo, and idempotent rerun behavior
    are covered without mutating candidate source rows.
- [x] P3b2b refund Review shadow backfill
  - Refund nature and every allocation now converge on one namespaced REFUND case
    per refund inflow. A refund split across several expenses is not fragmented
    into competing cases.
  - The current case has one full `REFUND_RECEIVED` line and one
    `REFUND_EXPENSE` line per currently confirmed allocation. Revoked allocations
    disappear from current lines but remain fully reconstructable in history.
  - Nature and allocation audits are merged chronologically into one version
    stream. Each before/after state, amount, operation, request, idempotency key,
    and allocation reversal pointer is validated and hashed.
  - The adapter verifies inflow/outflow directions, target fact existence,
    current designation versus latest nature audit, allocation row versus audit
    state, per-refund limits, and cross-refund expense limits.
  - Verified: 10 and 100 refund cases both execute exactly 11 SELECT statements.
    Partial allocation, allocation revocation, immutable history, source-row
    preservation, and idempotent rerun are covered.
- [x] P3b2c1 account Review shadow backfill
  - Explicit account corrections now become one namespaced ACCOUNT case per
    bill. The imported account remains raw evidence; an unedited source account
    is not mislabeled as a manual decision.
  - The current reviewed account stays in compact `result_json`, while the bill
    ID and full integer amount stay on one normalized ACCOUNT line.
  - Confirm and undo revisions become canonical history versions. The adapter
    validates the before/after chain, current `bills.account_name`, reversal
    pointers, final `undone` flags, requests, and idempotency identities.
  - Multiple sequential corrections remain active correctly: undoing the latest
    correction restores the preceding reviewed account rather than erasing the
    complete case history.
  - Verified: 10 and 100 account cases both execute exactly 9 SELECT statements;
    confirm/undo, exact reversal, source preservation, and idempotent rerun pass.
- [x] P3b2c2 tag Review shadow backfill
  - Bills with tag audit activity now map to one namespaced TAG case and one
    normalized full-amount bill line. No tag-specific lifecycle table was added.
  - Manual/authorised confirmations become authoritative case state. Local-rule
    and LLM suggestions remain proposals and never publish a ledger change.
  - Confirm, suggestion, and undo audits become one deterministic version stream.
    The adapter validates before-state continuity, the exact reversal target,
    lifecycle flags, current bill state, canonical requests, and idempotency keys.
  - Verified: 10 and 100 tag cases both execute exactly 9 SELECT statements;
    proposal preservation, confirm/undo restoration, source preservation, and
    idempotent rerun are covered.
- [x] P3b2c3 import-issue / fact-conflict Review shadow backfill
  - Import problem current state remains on `bill_raw`; no replacement
    `import_issue` table was introduced. One namespaced FACT_CONFLICT case points
    to the raw row and owns lifecycle/history only.
  - Pending and dismissed invalid rows do not fabricate bill lines. A resolved
    issue gains one full-amount `FACT_ACCEPTED` line only after `bill_fact` exists.
  - The adapter migrates the initial pending state plus resolve, dismiss, and
    reopen actions. It verifies valid state transitions, exact reopen reversal,
    final legacy resolution, target raw status, accepted facts, and source-row
    preservation.
  - Verified: 10 and 100 resolved issue cases both execute exactly 10 SELECT
    statements; resolve and dismiss/reopen paths plus idempotent rerun are covered.
- [x] P3c1 tag dictionary shadow backfill
  - Legacy `tag_views` and `view_tags` now map to the target `tag_view` and `tag`
    tables with stable IDs, explicit columns, and no SQL foreign keys.
  - Archived dimensions archive all of their values. Every dimension must have
    exactly one `unclassified` value before migration can match.
  - Legacy tag values have no timestamps, so the owning view's real creation time
    is used deterministically instead of the migration clock.
  - Verified: dictionaries with 10 and 100 views both execute exactly 6 SELECT
    statements; source preservation, drift comparison, and idempotent rerun pass.
- [x] P3c2 ledger-entry composition shadow backfill
  - Immutable facts are grouped only by confirmed financial Review connectivity.
    Pending/rejected/revoked Review never changes published cash legs.
  - Normal facts become INCOME/EXPENSE entries. AA, loans, refunds, transfers,
    and FX retain separate integer in/out legs; duplicate groups count only the
    retained fact.
  - Same-currency scales normalize exactly. Multiple currencies within one cash
    direction are migration blockers; cross-currency in/out directions remain
    separate and are never converted.
  - Stable input hashes cover every fact and confirmed Review input. Existing
    target drift is reported and never overwritten.
  - Verified: 10 and 100 facts both execute exactly 5 SELECT statements;
    composition, cross-currency, deduplication, idempotency, and drift pass.
- [x] P3c3 ledger lineage and tag projection
  - Every published fact and participating confirmed Review receives one
    deterministic `ledger_entry_source` row. The mapping is reversible from a
    single ledger detail to all immutable facts and Review evidence.
  - Every active tag view receives one effective value per ledger entry.
    Confirmed TAG Reviews override `unclassified`; pending suggestions do not.
  - Conflicting confirmed tags inside one combined ledger component block the
    shadow migration instead of choosing an arbitrary winner.
  - Verified: 10 and 100 facts both execute exactly 11 SELECT statements for
    entries, lineage, and tags. The current workspace produces 21 entries,
    21 fact sources, and 42 tag assignments with no blocker.
- [x] P3c4 integer hot-summary shadow comparison
  - The target summary uses only `ledger_entry`, explicit columns, and integer
    arithmetic. It performs exactly 2 SELECT statements for both 10 and 100
    entries.
  - An automatic comparison converts the current CNY dashboard basis to integer
    cents and checks income, expense, refund offset, and net. The combined old
    and target comparison performs exactly 9 SELECT statements at either size.
  - Foreign-currency target activity is reported as not comparable with the
    legacy mixed-currency dashboard; it is never silently converted or summed.
  - Current workspace shadow result matches exactly: 21 entries, CNY income 0,
    expense 38850, refund 0, and net -38850 (atomic scale 2).

## Next changes, in priority order

- [x] P3 — target-schema shadow migration
  - Target facts/raw evidence, unified Review adapters, tag dictionary, enhanced
    ledger composition, deterministic lineage/tags, and integer summary
    comparison are implemented without SQL foreign-key declarations.
  - Production API reads and the on-disk database remain unchanged. All writes
    in this phase target empty shadow tables or an in-memory copy.
- [x] P3d — target read/query shadow
  - Target list Mapper/VO/Service is implemented with exactly 3 SELECT statements
    for both 10 and 100 entries: count, explicit projection rows, and one
    `ledger_id IN (...)` tag query.
  - One-entry detail loading is implemented for facts, raw rows, import files,
    confirmed projection Reviews, related pending Reviews, lines, and complete
    history. A fully populated detail uses 10 bounded SELECT statements; absent
    optional import evidence naturally skips that one batch.
  - Current workspace in-memory shadow read returns all 21 projected entries in
    3 SELECTs. A reviewed entry exposes both related pending cases as non-source
    evidence, and no target read writes to the on-disk database.
  - Incoming/outgoing account codes are now hot projection fields. Confirmed
    ACCOUNT Review overrides immutable Fact account evidence; multiple accounts
    use `MULTIPLE`, missing evidence uses `UNKNOWN`, and source/import provenance
    remains a detail concern.
  - Read-only shadow endpoints are available under `/api/shadow/v1/ledger` for
    page, detail, summary, and readiness status. Existing UI/API routes are not
    switched.
  - The readiness gate checks Fact and Review lineage, tag coverage, and old/new
    summaries. It executes exactly 10 SELECT statements for both 10 and 100 rows
    and rejects a missing/partial target projection.
  - A single `TargetShadowMigrationService.backfill_and_compare()` orchestration
    and `scripts/backfill_target_all.py` provide an idempotent full migration
    entry point. No legacy row is updated or deleted.
- [ ] P3e — controlled on-disk shadow backfill and observation
  - Back up the SQLite file, apply additive account columns, and run the unified
    backfill against the real target tables.
  - Require `matched=true` and shadow status `ready=true`; retain the old API as
    the production source during an observation window.
  - Exercise real page/detail/summary shadow endpoints before proposing the UI
    read switch.
- [ ] P4 — retire compatibility storage after observation
  - Stop legacy writes only after shadow comparisons pass. Delete the three dead
    tag tables and other approved compatibility tables in a later explicit
    migration, never in the initial target-schema deployment.

Single-candidate, single-refund, and single-transaction detail endpoints are not
N+1 list paths. They remain lower priority unless profiling shows a slow query.

## Acceptance rules

- A list page returning 1, 10, or 100 rows executes the same number of SELECTs.
- No Mapper issues SQL while iterating over returned rows.
- List and summary queries use explicit columns and never load raw payloads or
  historical snapshots.
- Detail queries are bounded to one explicitly requested entity/case.
- New UI code uses paginated endpoints; compatibility endpoints remain temporary.
- API response snapshots and existing regression tests must remain unchanged.

## P3e/P3f completion update (2026-09-12)

- [x] Empty development database reset authorised. There is no production data;
  the user explicitly chose a destructive reset without backup or legacy-row
  backfill. Real samples are verified in a separate temporary database first.
- [x] Multi-source parsing merged from
  `9ef5bf5a440fb311a24d2e80e3c543e250c5d90e` for Alipay, WeChat, CCB, ABC,
  and CMB.
- [x] Preview derives identity keys, references, and a date window first. It no
  longer loads all bills, evidence, origins, identities, or artifacts.
- [x] Confirmation batch-loads referenced accounts, bills, bindings, origins,
  identities, review protection, and evidence state, then flushes grouped
  writes. A regression gate proves that 20 rows do not add per-row SELECTs.
- [x] Import API now follows Controller → Service → Mapper → DB. Existing UI
  routes remain compatible; versioned `/paam/import/v1` routes return the stable
  `{status,message,body}` envelope.
- [x] Seven supplied files produce 827 evidence rows and 803 canonical
  transactions in an empty temporary DB. Sequential reverse-order import,
  confirmation replay, and reverse-order upload are idempotent.

## P3g model-boundary correction

- [x] Reclassified `accounts`, `account_bindings`, `import_evidence`,
  `import_identities`, and `import_previews` as post-merge compatibility tables,
  not PIRC-9 target tables.
- [x] Move the versioned multi-source command path directly onto
  `import_file / bill_raw / bill_fact`; keep preview state outside ledger SQL.
- [x] Switch versioned import detail and account evidence reads to `bill_raw` and
  `bill_fact.account_code`.
- [x] Rebuild the affected default `ledger_entry` rows in the same confirmation
  transaction. Refunds publish as `REFUND`; neutral flows publish as
  `UNRESOLVED/PARTIAL` instead of entering ordinary income or expense.
- [x] Keep confirmation set-oriented. A regression gate proves that 20 target
  rows do not add per-row SELECTs, and all target SQL names explicit columns.
- [x] Add a target-only FastAPI runtime seam and empty-schema initializer. Its
  database contains exactly the 11 PIRC-9 tables and exposes only versioned
  import and ledger-read routes.
- [x] Verify all seven supplied files through the target write path: 827 raw
  rows become 803 facts and 803 ledger projections while every compatibility
  table remains empty.
- [x] Persist row-level immutable-fact conflicts as `bill_raw` with
  `parse_status=INVALID`, `issue_code=FACT_CONFLICT`, and `bill_id=0`. Valid
  rows in the same confirmation remain atomic; a conflict never fabricates or
  overwrites a `bill_fact`.
- [x] Switch financial Review commands to the target Review and Ledger tables.
  `AA / LOAN_BORROW / LOAN_LEND / REFUND / TRANSFER / FX_EXCHANGE / DUPLICATE`
  share one create/update/confirm/revoke/restore lifecycle. Commands batch-load
  facts and active owners, append canonical before/after history, and rebuild
  the affected hot component in the same transaction.
- [x] Add target tag-dictionary commands. A view owns one protected
  `unclassified` value, and creating/restoring a view assigns missing defaults
  to all current ledger entries with set-oriented SQL.
- [x] Add fixed-query Review/tag gates: Review list remains three SELECTs for
  twenty cases; confirming one or twenty facts executes the same SELECT count;
  tag dictionary list remains two SELECTs.
- [x] Define and implement TAG Review assignment semantics for a ledger entry
  that combines several facts, including how assignments split on revoke.
  - One ledger-level API command batch-loads all source Facts and writes the
    identical confirmed TAG state into one unified Review case per Fact.
  - A split restores each Fact's state; a restore/merge republishes one state
    only when every Fact agrees. Conflicts abort the financial confirmation and
    roll back its projection transaction.
  - Assignment uses optimistic `projection_version`, command idempotency, and
    append-only canonical before/after history. One and twenty source Facts both
    execute exactly six SELECT statements, with no `SELECT *` or row query loop.
- [ ] Switch ACCOUNT correction and FACT_CONFLICT resolution commands to
  unified target Review. Conflict evidence persistence is already target-only.
- [ ] Switch the production UI from legacy transaction/review/tag contracts to
  the versioned target contracts.
- [ ] Recreate the empty development database without the 23 legacy tables or
  the five post-merge compatibility tables. Preserve `asset_snapshots` only as
  an explicitly separate module.

The physical delete remains intentionally last. The target-only runtime now
proves that the replacement import/list/detail/summary and financial Review core
does not require a legacy table. TAG assignment, ACCOUNT/FACT_CONFLICT commands,
and the UI switch are the remaining functional dependencies, not a data-migration
or backup dependency.
