# PIRC-35 implementation baseline

Observed on 2026-09-30. The implementation target is `main` at
`38474fc55bdf247e868694b9a44dd2736350a7da` (the merged PIRC-24 change).
`feature/PIRC-35` starts at that commit. The PIRC-35 requirement and solution
documents in MPA are the approved behavior; this page records what the selected
PAAM commit already does and what its implementation still needs.

## Reuse and change matrix

| Area | Existing behavior at the selected commit | PIRC-35 disposition | Evidence |
| --- | --- | --- | --- |
| Money and source allocation | Integer money with currency precision; each Ledger output belongs to one Fact; exact allocation coverage is checked | Reuse and regress. Adapt the public names and new relationships; do not replace the money conversion | `src/backend/core/money.py`, `src/backend/entity/review_allocation.py`, `src/backend/service/target_economic_service.py::_prepare/_assert_exact` |
| Import and bank parsing | Preview, revise and confirm use `parse_statement`; CCB, ABC and CMB have parser branches; accepted rows retain source evidence | Reuse the parser and file/row identity. Adapt first-import default creation, account-ref binding, bounded batches and result-unknown handling. A target bank format needs an authorized redacted sample before claiming support | `src/backend/service/target_intake_service.py`, `src/backend/parser/statement_parser.py`, `src/test/test_target_import.py` |
| Review and cash Ledger | Create, revoke and restore run in a write transaction and support the old command replay. A partial manual allocation leaves a residual DEFAULT | Replace the old partial-coverage behavior with the confirmed one-current-Review contract, immutable published content and atomic activation/deactivation. Preserve useful transaction and audit code; old replay fields are not the target contract | `src/backend/service/target_economic_service.py`, `src/test/test_ledger_api.py::test_partial_manual_reviews_keep_exact_default_coverage_and_are_idempotent` |
| Account identity | Ledger carries an editable `account_code`; source import records some account information | Add the approved party/account/ref identities and source binding. A Ledger `account_code` edit cannot establish stable account identity | `src/backend/service/ledger_account_service.py`, `src/backend/entity/ledger_entry.py` |
| Position and second allocation | No persisted Position, PositionLeg or Ledger-to-leg allocation exists in this candidate | Add the approved Position, leg and second allocation chain, including quantity and evidence validation | `src/backend/entity/`, `src/asset/sql/` |
| Tag approval and scanning | PIRC-24 rule, request, approval and scan/epoch code is present after the merge; approval checks rule revision, active Ledger/View, dictionary and manual conflicts | Reuse the installed consumer. Adapt eligibility, request invalidation and scan rewind in the same Review publication transaction; exclude DUPLICATE from automatic candidates. Verify the integrated API and scheduler on the eventual implementation candidate | `src/backend/service/tag_assignment_request_service.py`, `src/backend/service/auto_tag_scan_service.py`, `src/backend/mapper/auto_tag_scan_mapper.py` |
| List and search | Existing Ledger and import pagination/query paths exist | Adapt them to the confirmed PIRC-35 exact-total/list and bounded cursor-search contracts without treating an early batch as all results | `src/test/test_ledger_api.py`, `src/test/test_target_import.py` |
| Existing data | No PIRC-35 migration has run on this branch | Preflight old Review/Allocation/Fact relationships on a consistent redacted copy, then implement the approved rename and new tables with rollback and restoration checks | PIRC-35 `SOL-007`; current `src/asset/sql/` |

## Baseline verification and open inputs

- `python -m pytest src/test/test_ledger_api.py src/test/test_target_import.py src/test/test_auto_tag_scan.py src/test/test_tag_assignment_request_api.py -q` passed: 80 tests on this commit. These are existing-behavior regressions, not PIRC-35 acceptance.
- No `.csv`, `.xlsx`, `.xls`, `.zip`, `.sqlite` or `.db` sample was found under the checked-out MPA/PAAM workspaces. The tests construct synthetic input. A target bank's authorized redacted statement and a redacted old-data migration fixture remain unverified.
- PIRC-24 being merged removes the old “consumer not installed” assumption. The PIRC-35 tag protocol still needs same-candidate integration tests for Review changes, rollback and scanner invalidation.
- This branch's draft PR is the review and merge object. Implementation starts only after PIRC-35 `DEV-01` and `GATE-01` have their recorded completion evidence.

## PIRC-24 adapter details

The installed `auto_tag_rule` and `tag_assignment_request` columns match the
consumer shapes described by PIRC-35 `SOL-006`. The request table already has
`(ledger_id, view_id, status)` and `(rule_id, status)` indexes. The installed
service retires pending/enabled requests for affected Ledger IDs; the rule
mapper rewinds the cursor and increments `scan_epoch`. The scheduler also obeys
a global `scan_enabled` setting, so a present rule is not proof that scanning
is running. Current approval validates the rule revision, active Ledger/View,
target dictionary and manual assignment conflict. It has no PIRC-35
`DUPLICATE` category to exclude yet. The new Review publisher must call these
consumers in its own transaction and verify both publication/approval orders.

One read-only aggregate of the currently running local database found 255
Facts, Reviews, allocations, Ledgers and import rows, with no broken links,
duplicate Ledger allocations or active coverage mismatch. Its one import file
uses source type `102` (WeChat); it supplies no bank format example. The live
database is not a redacted migration fixture and must not be used as a mutable
test database.
