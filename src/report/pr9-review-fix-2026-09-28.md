# PR #9 review fixes

Base: `43433f055e17aa437bcfbb1117c2af79aaffff4d`.

## Confirmed Actions failure

[Run 36401047158](https://github.com/HuAzurestar/personal-assets-ai-manager/actions/runs/36401047158)
failed in `pytest -q`: **564 passed, 1 failed**. The only failing test was
`test_target_runtime_uses_only_pirc9_tables_and_routes`, asserting the removed
copy “等待不会产生新建议”. Dependency installation succeeded. The Node 20
action deprecation was a warning, not the cause. Windows packaging is tag-only
and was intentionally skipped on this PR.

Updated the stale assertions while retaining API, disabled-scan and UI contract
coverage. Actions now also runs four Node suites and four fictional-data browser
regressions, and retains the pytest XML report. The browser job pins Ubuntu 22.04
with the existing Playwright 1.45.0 toolchain.

## Product fixes

- Compare effective model parameters, including the adapter's timeout default,
  rather than field presence. Sparse/default/null GET→PUT roundtrips, renames,
  enable/disable and credential changes preserve rule revisions, progress and
  pending suggestions. Zero and false remain meaningful parameter changes.
- Model forms retain their original configuration token. Genuine parameter
  changes first read affected rules and pending suggestions, display the impact,
  and require acknowledgment. Editing again clears that acknowledgment.
- Restart the current automation page's read-only poll after a failed explicit
  refresh, mark its contents stale and block approvals until a successful read.
  Remove the refresh error when the page recovers; never retry write commands.
- Paginate/search/filter the rule list, read selector options in API pages,
  and locate newly created rules even beyond the original first 100 records.
- Use the shared display-timezone formatter for automation dates, previews and
  diagnostics. CRON execution still uses its explicit Hong Kong timezone.
- Constrain the runtime grid and stack diagnostic/search controls on narrow
  screens. This fixes the 390px settings overflow exposed after removing an
  obsolete fixed-height assertion.
- Clarify that a disabled service-level scan needs service configuration and
  restart; enabling a model or rule alone does not start paid analysis.

Correction to the earlier review: tag views have an intentional backend maximum
of 100. That limit remains; the regression verifies all 100 selectable views.
Rules have no equivalent limit and are tested at 101/102 records.

## Verification

- Full backend suite: **570 passed**, one existing Starlette/AnyIO deprecation warning.
- Four Node suites passed: automation UI, M2 UI, settings form, tag assignment.
- `verify_pr9_fix.py` passed: harmless rename, explicit model-change impact,
  stale form and credential protection, network recovery, timezone consistency,
  rule pagination/search, complete selectors, and new-rule navigation.
- `verify_automation_repair.py`, `verify_refresh_ui.py` and
  `verify_automation_presentation.py` passed, including 390/768/1440 widths,
  four themes, focus, input drafts and scroll preservation.
- Changed Python files passed Ruff; JavaScript syntax, workflow YAML parsing
  and `git diff --check` passed.

SDK transport tests now use a local byte tokenizer and reject Requests network
access, so an empty tokenizer cache cannot turn an offline test into a download
through the deliberately invalid test proxy. This changes the test fixture only,
not the application's provider requests or classification tokenization.

All browser/API mutations used newly created fictional databases. No deployment
was switched, no live model was called, and no business data or real credentials
were changed.
