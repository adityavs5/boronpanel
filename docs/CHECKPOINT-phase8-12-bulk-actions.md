# CHECKPOINT phase8-12 — Bulk account operations

**Goal:** multi-select suspend/unsuspend/update-limits/send-notification; async,
per-account progress, stop on first failure.
API: `POST /admin/accounts/bulk-action`.

## What was built

- **Model** `BulkActionJob` (action, action_params, status, total,
  completed_count, current_username, results[], error) — same async-job shape as
  NamespaceMigrationJob.
- **Daemon** `daemon/bulkops.py` (ops `bulk.trigger/get`): a **single-worker**
  executor runs the accounts **strictly sequentially** and **stops at the first
  failure** (records the failing account and returns; later accounts are never
  touched). Each action reuses the existing audited per-account handler
  (`handlers_account.suspend_account/unsuspend_account/set_limits`) or
  `notifications.send_direct` for notify, so bulk behaves identically to doing
  them one-by-one. action_params validated up front (notify needs subject+body;
  update_limits needs ≥1 field).
- **API** `api/routers/bulkops.py` — `POST /admin/accounts/bulk-action`,
  `GET /admin/accounts/bulk-action/{job_id}`, both `require_admin`.
- **Frontend**: admin Accounts page gains **row checkboxes + select-all** and a
  **bulk action bar** (action picker, conditional limits/notify inputs, live
  per-account progress with ✓/✗ per account).

## "2 accounts suspended simultaneously" + stop-on-failure (Done-When)

`test_bulk_suspend_two_accounts` suspends two accounts in one job (completed,
2/2). `test_bulk_stops_on_first_failure` confirms that when acc2 fails, acc3 is
**never attempted** and the job ends `failed` with the error recorded.

## Tests

`tests/test_bulkops.py` — 7 tests: validation (bad action, empty usernames,
notify requires subject+body), two-account suspend, stop-on-first-failure,
notify via send_direct, update_limits. All green.
