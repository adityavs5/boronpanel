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

## Post-review fix (same day): cleanup could void the rollback window

A fresh-eyes review pass after the feature landed found that
`cleanup_old_versions` judged prunability by directory **mtime** alone.
The first-ever update *converts* the months-old `/opt/forgehost`
directory into the rollback target — whose mtime long predates the
update — so the very next nightly cleanup would have pruned the rollback
target immediately, silently reducing "rollback within 3 days" to
"rollback until 05:25 tomorrow". Fixed with two layers: (1)
cleanup now also protects any dir referenced (old_dir/new_dir) by a job
completed within the retention window — a DB-backed guard that doesn't
depend on filesystem timestamps at all; (2) the finalizer bumps the old
dir's mtime on success so the timestamp becomes meaningful anyway.
Regression test: an old-mtime dir referenced by a just-completed job
survives cleanup; once the job ages past the window it is pruned.
