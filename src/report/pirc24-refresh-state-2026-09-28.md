# PIRC-24: preserve interaction state during refresh

Implementation baseline: `0201c93`. User requested automatic and post-command refresh throughout the UI, retaining cursor and viewport position. This is a frontend-only change; no scan cursor, database, provider or production configuration is modified.

## Changes

- Shared presentation-state helper retains expanded details, eligible selection, field values/caret, focus, nested scrolling and a visible row's viewport offset. If the focused row disappears, focus moves to a surviving next/previous control. Disabled controls remain authoritative.
- Automation regions use keyed DOM updates. Rules, requests, diagnostics and settings can update without replacing unchanged rows. Settings also re-read saved model/disclosure metadata.
- Same-route command refresh keeps the previous page visible while loading; failures retain content and show a warning. Normal navigation still loads the new route.
- Read-only overview, fact, economic, review, tag and import lists refresh periodically. Hidden pages, active dialogs/calendar popovers, focused editors, inline drafts and pending commands suppress background replacement. Import and review wizards retain their draft-driven lifecycle rather than being rebuilt periodically.
- Import filters/page selection, preview recalculation, ledger editor rows, same-record inspection refresh and calendar updates share position protection. Navigation controls are not rebuilt on same-route refresh.
- Mutation lifecycle events invalidate older polling/page reads. Background polls do not invalidate explicit detail requests. No mutation is retried by the refresh mechanism.
- Frontend module cache versions advance to `20260928.1` for this candidate; recheck versions on integration because concurrent PR changes may use the same suffix.

## Verification

- Existing four Node suites pass, including added stale-response/command-in-flight polling checks.
- Existing target and M2 Edge workflows pass, preserving business assertions.
- Repair Edge workflow passes (settings, amounts, model checks, three viewport sizes).
- New `verify_refresh_ui.py`: two automatic rule refreshes, keyed row identity, expanded state after save, periodic and post-archive tag refresh in a long list, inline drafts, caret selection, inserted/removed-row anchoring, focus fallback, nested scrolling and disabled selections.
- All fixtures are disposable and fictional; no real model calls or production writes.

## Integration boundary

PR #9 advanced during implementation to `e822bfb`, including changes to the same automation rendering paths. Save the candidate, run read-only merge preflight, and request user direction for any conflicts. Do not force-push or silently discard either side. The above checks are candidate checks, not proof of the as-yet-unperformed integration.
