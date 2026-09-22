---
name: job-schedule
description: Design or change PAAM recurring background work, CRON schedules, timer-driven jobs, startup recovery, or schedule registration.
---

# Shared job scheduling

## Status and scope

This is the project-wide scheduling rule requested during PIRC-24 design.
The shared module is `src/backend/core/job_scheduler.py`. Extend that module
instead of creating a feature-owned scheduler or timer.

## Single entry point

- All backend CRON, recurring timers, scheduled scans, and retention jobs must
  register through the shared module. Do not create a scheduler per feature,
  start an independent sleep-loop, or invoke business callbacks directly from
  an HTTP route.
- Own one process-local scheduler in `backend/target_main.py`'s lifespan.
  Business Services own callbacks; Mappers own queries and checkpoints.
- Use stable namespaced registration keys, e.g. `tag-scan:{rule_id}`.
  The intended operations are register, update, remove, pause, and preview.
- Use a verified stable APScheduler release. Do not switch to prerelease APIs
  merely because their major number is higher.
- Do not introduce Redis, Celery, a second deployed service, or a persistent
  scheduler job store without a separate architecture decision.

## Execution and recovery

- Reconstruct registrations from persisted business settings at startup.
  Coalesce missed ticks; do not replay every missed tick after downtime.
- Bound overlap, concurrency, execution time, page size, and retry count.
  Re-read enabled state at execution; pause must prevent new external calls.
- Keep network requests and archive I/O outside database write transactions.
  Save business results and the relevant scan checkpoint atomically.
- A checkpoint reset must invalidate an in-flight worker's old checkpoint
  token. Explain the concrete race before adding version/epoch fields.
- Stop accepting work before shutdown and explicitly cancel queued and
  in-flight calls. Never report a cancelled call as a committed result.
- A manual trigger, if explicitly required, must use the same scheduling
  module and concurrency gate. Do not add manual run/rescan APIs by default.
- Ordinary HTTP request timeouts and frontend debouncing are not recurring
  background jobs and do not need this module.

## Verification

Cover restart registration, invalid CRON, timezone interpretation, duplicate
ticks, pause during a call, checkpoint reset during a call, and shutdown.
Keep API paths and response contracts consistent with the router-layer Skill.
Do not predesign future review-case candidate algorithms in the scheduler.
