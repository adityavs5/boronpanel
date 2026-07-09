# CHECKPOINT run-a-1 — Plan templates

**Goal:** named limit presets (Basic/Pro/Business): CPU%, RAM, IO, pids,
disk, bandwidth, max DBs/mailboxes/subdomains/FTP/apps, Redis y/n. Stored
in panel DB. Apply on account create or via "Apply plan" button. Limits
set atomically. API: CRUD `/admin/plans`, `POST
/admin/accounts/{u}/apply-plan/{id}`.

## What was built

- **Model** `Plan` (shared/models.py): name (unique) + its own copy of
  every limit value (cpu_pct/mem_mb/io_mb/pids_max/quota_soft_mb/
  quota_hard_mb mirroring `Account`'s own columns; bandwidth/database/
  email/subdomain/ftp/app limits mirroring `AccountResourceLimits`'
  nullable-means-unlimited convention; `redis_enabled` bool). A plan is a
  template, not a live binding — editing a plan later never retroactively
  changes any account already built from it.
- `Account.plan_id` (nullable FK): purely informational "current plan"
  label for the UI, never read back to derive enforced state (the actual
  enforced values always live on `Account`'s own limit columns,
  `AccountResourceLimits`, `RedisInstance`).
- `AccountResourceLimits` gained two new nullable columns:
  `ftp_account_limit`, `app_limit` — no existing quota column covered
  FTP/app counts. Both tables predate this feature, so both new columns
  (`accounts.plan_id`, `account_resource_limits.ftp_account_limit`/
  `app_limit`) are registered in `shared/db.py`'s `_ADDITIVE_COLUMNS` for
  existing installs.
- **Daemon** `daemon/plans.py`: `create_plan`/`list_plans`/`get_plan`/
  `update_plan`/`delete_plan` (straightforward CRUD; `delete_plan` clears
  `Account.plan_id` on any account referencing it first, rather than
  hitting a raw FK-constraint `IntegrityError` — the same bug class
  already fixed once for `Webhook` deletion). `apply_plan` is the one
  operation that actually mutates an account:
  1. Validates the account (must be `active`/`suspended`) and plan exist.
  2. Writes `Account`'s six limit/quota columns + `plan_id` in one
     `write_session` — this is the "atomic" part: it all commits or
     rolls back together.
  3. Calls `daemon.usage_alerts.set_limits` (its own transaction) for the
     six `AccountResourceLimits` fields — reuses that module's existing
     validation/write logic rather than duplicating it.
  4. Fires the existing `LIMITS_HOOKS` (cgroup reconcile), calls
     `sysops.set_quota`, and calls `redisacct.enable_redis`/
     `disable_redis` to match `redis_enabled` (a "disable" is a no-op if
     Redis was never provisioned for the account, so a redis-off plan
     applied to a fresh account never raises).
  No new logic was written for cgroups, quota, or Redis provisioning —
  `apply_plan` only orchestrates calls into the modules that already own
  each of those.
- **API** `api/routers/plans.py`: `POST/GET /api/v1/admin/plans`,
  `GET/PATCH/DELETE /api/v1/admin/plans/{id}`, `POST
  /api/v1/admin/accounts/{username}/apply-plan/{plan_id}` — all
  `require_admin`, no customer access (plans are an admin-configuration
  resource; an account's own *current* limits are already visible via the
  existing usage-limits/account endpoints).
- **Apply on create**: `CreateAccountBody` gained an optional `plan_id`.
  `api/routers/accounts.py`'s `create_account` calls `account.create` then,
  if `plan_id` was given, `plan.apply` as a second RPC call. If the second
  call fails the account still exists (matches this project's existing
  "a failed step doesn't silently undo a previous one" philosophy, e.g.
  `terminate_account`'s fixed-order teardown).
- **Frontend**: new admin page `Plans.jsx` (`/plans`, nav entry under
  Administration) — CRUD table + create/edit dialog + delete confirm,
  same `DataTable`/`Dialog`/React-Query pattern as `IpWhitelist.jsx`.
  `AccountDetail.jsx` gained a "Plan" card (current plan name + a
  plan Select + Apply button) in the Overview tab. `Accounts.jsx`'s
  create-account dialog gained an optional plan Select.

## Tests

`tests/test_plans.py` — 14 tests: CRUD (create/get/list-sorted/duplicate-
name rejection/invalid-cpu_pct rejection/inverted-quota rejection/partial
update/unknown-plan-on-update), `delete_plan` clearing `Account.plan_id`,
and `apply_plan`: every field set atomically (all six `Account` columns +
all six `AccountResourceLimits` fields + `plan_id` in one call), Redis
enable firing for a Redis-on plan, Redis staying untouched for a Redis-off
plan on an account that never had it, re-applying a *different* plan
overwriting (not adding to) the previous values, and rejections for an
unknown account, unknown plan, and a terminated account.

Full suite: 1463 passing (1449 baseline + 14 new), confirmed via a
from-scratch run after every change in this checkpoint.

## Frontend build

`cd frontend && npm run build` — clean, no errors (`Plans-*.js` chunk
generated, ~7.3 kB gzip ~2.55 kB). Not deployed to `/opt/forgehost` or
exercised in a browser — that requires `scripts/deploy.sh`, which needs
separate user approval per this project's established deploy convention.

## What's honestly still open

- FTP-account and app counts (`ftp_account_limit`/`app_limit`) are stored
  and displayed but **not enforced** at creation time (no code currently
  blocks creating a 4th FTP sub-account against a plan's limit of 3) —
  same status as bandwidth/database/email/subdomain limits already had
  before this feature (Phase 7b feature 5 only *alerts* at 80/90/100%, it
  doesn't hard-block). Out of this feature's stated scope (goal says
  "max ... FTP/apps" as plan fields to store/apply, not "enforce"), but
  worth flagging for a future feature if hard caps are wanted.
- Not live-verified against the running `/opt/forgehost` deployment
  (blocked on deploy approval, per `[[forgehost-deploy-flow]]`) — backend
  + tests + frontend build are all green locally.
