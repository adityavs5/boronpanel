# CHECKPOINT — Panel Update System, features 5+6: rollback + history

Date: 2026-07-10.

## Rollback (feature 5)

- `update.rollback` op → `rollback_candidate()` finds the newest
  completed, not-already-rolled-back update job whose `new_dir` is what
  the live symlink points at **right now** and whose `old_dir` still
  exists on disk (pruned after `update_keep_old_days` — that's the "within
  3 days" bound; after pruning the op explains exactly why rollback is
  unavailable).
- Sanity gate before handoff: the rollback target must still carry
  version.py, .venv/bin/python and daemon/server.py.
- Same detached finalizer in `--mode rollback` — swap back, restart both
  panel units, health check; a rollback that fails ITS health check swaps
  forward again (never leaves the panel on a half-dead target).
- `POST /api/v1/admin/update/rollback {confirm, totp_code?}` — same
  admin + confirm + conditional-2FA gate as start (a rollback is exactly
  as sensitive as an update; the goal names 2FA only for update, applying
  it to both is the conservative reading, documented here).
- Rollback attempts are UpdateJob rows too (kind="rollback") → they appear
  in history with from/to/duration/who like updates.

## History (feature 6)

- `update.history` → UpdateJob rows newest-first (from_version,
  to_version, status, rolled_back, initiated_by, started_at,
  completed_at, duration_seconds, per-step log). The job table IS the
  history table — no second bookkeeping path to drift.
- `GET /api/v1/admin/update/history?limit=` (cap 200).

## Tests

rollback_candidate found/none/pruned-dir cases; rollback job queueing;
live rollback-mode finalizer run in the /tmp sandbox (real swap back to
the old version dir, ORM-verified terminal state); history ordering +
duration; API gating incl. the 2FA path on rollback.
