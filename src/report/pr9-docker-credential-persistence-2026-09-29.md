# PR #9 Docker credential persistence verification

The PR's prior Docker image mounted SQLite at `/data` but selected
`keyring.backends.fail`, so model profiles persisted while API keys could not.
This change adds an opt-in encrypted-file store for headless Docker. The native
OS-keyring default, API shapes, rule state and SQLite schema are unchanged.

## Verification on the merged PR candidate

- PR remote head before integration: `9c2081c` (`feature-PIRC-24-m2-core`).
  The local candidate merged that head as `5e0b9f5`, with no conflicts.
- `python -m pytest src/test -q`: **600 passed**, one existing Starlette
  deprecation warning. Four Node UI suites and targeted Ruff checks passed.
- Built `paam:pr9-credential-5e0b9f5` from the merged worktree.
- On port 18780, a new disposable volume and a fictional API key: create
  disabled model → save key → enable model → GET returned `key_configured=true`.
  The volume held `/data/model-credentials.fernet` with mode `0600` and no
  plaintext fictional key. Recreating the container with the same volume and
  read-only mounted key kept `enabled=true` and `key_configured=true`.
  Recreating with a different key exited with status 1 instead of serving an
  empty credential state. Both analysis flags were explicitly set to `0`;
  no provider request was made.
- Port 18779 now runs the candidate image with the original preview data volume
  and a separate read-only mounted key. HTTP health is `ok`; its pre-existing
  model #1 remains disabled and unconfigured. Analysis flags remain `0`.
  The former preview container is stopped and retained for rollback.
- Port 18778 and its volume were not changed. No real API key, ledger data,
  model call or automatic approval was used for this verification.

The 18779 preview key is stored outside its Docker data volume in the local
operator profile. Back up the key separately if credentials are added later.
This disposable preview is not a substitute for a reviewed production secret
provisioning and backup procedure.
