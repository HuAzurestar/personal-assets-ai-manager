# PIRC-24 P0/P1 repair — 2026-09-27

## Scope

User-authorized repairs to PR #9, preserving concurrent DPI/navigation and readiness changes. This update does not deploy production, approve M2-CORE, or merge the PR into main.

| Area | Change |
| --- | --- |
| Settings | Three compact sections; advanced parameters and diagnostics collapsed; no ordinary demo entry |
| Scheduler | Distinguish unknown, disabled, waiting, queued, running and failed states; show next steps |
| Model | Simple editor with lossless advanced parameters; explicit, separate real connection check |
| Disclosure | Visual amount boundaries with exact decimal conversion and preview; unchanged save is a no-op |
| Rules | Common frequency presets, custom CRON roundtrip and existing prompt templates |
| Review | Responsive cards, linked evidence, selection-only batch toolbar, mobile select-all |

## Safety boundaries

- Connection checks require explicit confirmation and a current configuration token. They use a fixed short message, 32 output tokens, a 15-second SDK timeout, no automatic retry, and a concurrency guard. Provider output and credentials are not returned. Checks do not read ledger data or write analysis results.
- Configuration validation and version checks occur before credential mutation. A failed later credential step is reported as a partial save; a staged model remains disabled until credential installation succeeds.
- Existing automatic-tag approval and privacy protections remain in place. No migration or production data reset is included.
- Cache versions are advanced beyond the concurrent PR revision.

## Verification

- Four Node suites: automation, M2, tag assignment, exact amount/CRON forms.
- Edge: original target workflow, original M2 workflow, new repair workflow (including configuration conflict before secret write, unchanged advanced parameters, explicit single connection check, exact amount save and three viewport sizes).
- DPI audit: 11 configurations, 121 states, no layout/navigation/reachability failure and no external request.
- Connection-service tests include an actual SDK with offline HTTP 401, one request, sanitized errors, no SQL writes, stale configuration and concurrency checks.
- Full Windows and offline Linux source-mounted regression results are recorded in the DEV-015 project evidence after execution. The Linux run is not a rebuilt production-image deployment test.

All browser data is fictional. Provider responses are mocked or supplied by offline transports; these tests do not claim a successful live provider connection. Production scanning and credentials were not changed.
