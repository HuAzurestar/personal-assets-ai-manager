# PIRC-24 UI/DPI fixes — 2026-09-27

Baseline: `f67ec933d571516c6a33d753f13ab639089a7551`, PR #9 source `feature-PIRC-24-m2-core`.

## Changes

- P1: reveal the selected secondary navigation item on route changes and container resize. Only the navigation container scrolls; the document and keyboard focus stay put. Other navigation items remain available through horizontal scrolling.
- P2: align model/rule form label contents to the top so helper text no longer stretches adjacent inputs or shifts their baselines (previously 12–20.5 CSS px).
- P2: allow rule action buttons to wrap as complete buttons, keeping each short Chinese caption on one line.
- Refresh the HTML → entry → ledger → navigation import cache versions and the stylesheet version.

## Verification on the changed working tree

- `python src/script/audit_pirc24_dpi.py`: PASS, 132 states, zero browser errors and zero external requests. All 11 profiles pass form alignment, selected navigation visibility, single-line rule captions, page/dialog horizontal overflow and dialog button actionability checks. Live resizing 1920 → 960 → 390 → 1366 CSS px also keeps the selected rule navigation item visible.
- Profiles: 1366×768 at 100/125%; 1920×1080 at 100/125/150/175/200%; 2560×1440 at 150%; 3840×2160 at 200%; 390×844 and 768×1024 at 100%.
- `python src/script/verify_target_ui.py`: PASS, import/fact/review/economic flow and 13-table isolation.
- `python src/script/verify_m2_ui.py`: PASS, persisted disclosure, batch partial success, approval/rejection, draft protection, polling/offline feedback and scheduler diagnostics. Provider calls: zero.
- `node src/test/automation_ui.cjs`, `automation_m2_ui.cjs`, `tag_assignment_ui.cjs`: all PASS.
- Navigation syntax, audit-script Ruff checks and `git diff --check`: PASS.
- The earlier baseline full Python run passed 545 tests. It was not rerun for this frontend-only fix.

All browser checks use disposable fictional data and Edge 153.0.4234.48. DPI is simulated with device scale factor plus the corresponding reduced CSS viewport, not a physical Windows display-setting change. The audit writes screenshots and JSON under `src/report/pirc24-ui-dpi-fixed/` (override with `PAAM_DPI_EVIDENCE_DIR`); generated evidence is not included in the commit. No real ledger, credentials, provider calls or acceptance gates are changed.
