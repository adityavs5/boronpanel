# Checkpoint: Phase 2, Feature 2 — Cron job UI

## What was built

- `daemon/cron.py` — the real system crontab (`crontab -u <username>`) is
  the only source of truth, no shadow table in the panel DB. Jobs are
  identified by a marker comment Forgehost writes immediately above each
  job it manages (`# forgehost:id=<uuid> label=<label>`), so add/update/
  delete can target exactly one job without disturbing any other line in
  the account's crontab — verified this actually holds for unrelated,
  manually-added lines too (`test_unmanaged_lines_are_preserved`).
- Validation via `croniter` (5-field cron expression syntax) plus
  Forgehost's own checks: non-empty command, no newlines in
  command/label (not a privilege escalation — it's the account's own
  crontab — but a newline would let one `add_job` call silently smuggle in
  a second, unvalidated line).
- `daemon/handlers_cron.py` — RPC layer (`cron.list`/`add`/`update`/
  `delete`), account-status-gated same as every other handler, plus a
  `terminate_account_cron` hook (crontab removal is idempotent — safe even
  if the account never had one).
- `PATCH`-style CRUD REST API at `/api/v1/accounts/{u}/crons` + a
  server-rendered UI: a table of existing jobs, and an add form with a
  **5-field schedule builder** (minute/hour/day-of-month/month/day-of-week,
  each defaulting to `*`) alongside a raw-expression override field that
  takes precedence when filled in — satisfies "schedule builder alongside
  raw cron expression" without needing any JS (a deliberate continuation
  of Phase 1's "plain forms, no JS framework" choice).
- 25 new unit tests (16 for `daemon/cron.py`'s parsing/validation/CRUD
  logic against an in-memory fake crontab, 5 for the RPC handler layer, 4
  for the schedule-builder's field-combining logic).

## A real deployment bug found immediately on restart (not a logic bug)

Installed `croniter` into the **dev** venv (`/root/cpanel-clone/.venv`)
but not the **deployed** venv (`/opt/forgehost/.venv`) — `scripts/deploy.sh`
only rsyncs code, it was never supposed to touch `.venv` (a previous Phase
h bug came from exactly the opposite mistake, sweeping `.venv` with a
blanket chmod). Restarting `forgehost-provisiond` crash-looped
(`ModuleNotFoundError: No module named 'croniter'`) until noticed via
`systemctl status`/`journalctl`. Fixed immediately (installed into the
deployed venv, added `croniter` to `requirements.txt`), then fixed
**structurally** so it can't happen again silently: `scripts/deploy.sh` now
runs `pip install -r requirements.txt` against the deployed venv on every
invocation (fast/no-op when nothing changed, since pip just checks
versions against what's already satisfied).

## Real end-to-end verification performed

1. Created a real account, added a job via the REST API with a raw
   expression (`*/5 * * * *`) — confirmed the **exact** expected line
   appeared in a real `crontab -l -u crontest` call, immediately below the
   correct marker comment.
2. Added a second job via the **UI's schedule builder** (not the raw
   field) with minute=0, hour=3, dow=0 — confirmed the server correctly
   composed `0 3 * * 0` and it appeared correctly in the real crontab
   alongside the first job, both independently listed via the REST API.
3. Deleted the first job via the REST API — confirmed via real
   `crontab -l` that *only* that job's two lines were removed, the second
   job's lines untouched.
4. `account.terminate` — confirmed `crontab -l -u crontest` now fails with
   "user `crontest' unknown" (the account and its crontab are both
   genuinely gone, not just emptied).
5. Full 191-test suite (166 existing + 25 new) passing.

## What's untested

- No test exercises a job whose `command` itself invokes something
  resource-intensive (interacts with Feature 6's cgroups work, still
  upcoming in this phase) — cron jobs currently run with no cgroup
  assignment at all; whether/how Feature 6 should extend limits to
  cron-spawned processes is an open question to resolve when building
  that feature, not decided here.
- No UI affordance to *edit* an existing job's schedule/command in place
  (the API's `PUT /api/v1/accounts/{u}/crons/{job_id}` exists and is
  tested; the UI only exposes add/delete). Low-risk omission — delete +
  re-add achieves the same result from the UI today.
