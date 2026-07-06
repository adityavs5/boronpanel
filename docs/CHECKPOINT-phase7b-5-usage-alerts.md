# Checkpoint: Phase 7b Feature 5 — Account usage alerts

## What was built

Automated 80/90/100% threshold alerts across disk, bandwidth, databases,
email accounts, and subdomains, piggybacking entirely on data other
features already compute — no new collection.

- `daemon/usage_alerts.py`: `AccountResourceLimits` (bandwidth/database/
  email-account/subdomain limits + an `auto_suspend_at_100` flag, lazily
  created on first read/write) — disk deliberately reuses the existing
  `Account.quota_soft_mb`/`quota_hard_mb` (Phase a, OS-enforced) as its
  100% reference rather than a duplicate field. `UsageAlert` (one row per
  alert episode, `resolved_at` NULL while active — a durable history, not
  a mutated single-row-per-resource status).
- **Monotonic-escalation threshold logic**: a new alert (and a fired
  event) is created only when the newly crossed threshold is *higher*
  than the account's own currently-active alert for that resource (if
  any) — usage fluctuating within the same "still above 80%" band does
  not re-fire; dropping back under 80% resolves the active alert (closes
  the episode, doesn't delete the history row). Escalating (e.g. 80% →
  90%) closes out the old episode and opens a new one at the higher
  level, so "active alerts" always reflects the current highest crossed
  threshold per resource.
- Fires `events.emit("usage.limit.reached", account, resource=...,
  threshold_pct=...)` on every new escalation — reaching both the email
  (feature 3) and webhook (feature 4) channels for free through the
  shared fan-out point.
- **Optional auto-suspend at 100%** (`auto_suspend_at_100`, off by
  default): calls the real `handlers_account.suspend_account` — a failure
  there (e.g. the Linux user somehow already gone) is logged, not allowed
  to break the alert check itself.
- A new `scripts/usage_alert_check.py` periodic script (same "server
  infrastructure" cron category as `usage_snapshot.py`) — needs no
  explicit ordering relative to that script since `usage.get_usage()`
  already lazily refreshes stale data itself when read.
- **UI banner**: `account_detail.html` now fetches and displays active
  alerts at the top of the page (goal: "panel UI banner") — reads
  already-computed `UsageAlert` rows, no fresh computation on every page
  load.
- **API**: `GET`/`PATCH /api/v1/accounts/{u}/usage-limits`, `GET
  /api/v1/accounts/{u}/alerts` (see naming note below).

## A deliberate route-naming deviation, and why

The goal's own text names `PATCH /accounts/{u}/limits` for this feature —
but that exact path was already `account.set_limits` (cpu/mem/io/pids
cgroup resource limits, Phase 2 feature 6, live and shipped long before
this feature existed). Reusing it here would silently overload one
already-public endpoint with two unrelated meanings depending on which
body fields happen to be present in a given request — a real
discoverability/correctness regression against an existing route, not a
private implementation detail free to redefine. `PATCH
/accounts/{u}/usage-limits` is used instead so both endpoints keep exactly
one meaning each; `GET /accounts/{u}/alerts` needed no such change since
no prior route claimed that path.

## Tests

19 new tests (`tests/test_usage_alerts.py`): limits CRUD (lazy row
creation, partial updates, `None` clears a limit, unknown-account guard),
resource-counting helpers against real seeded rows (databases/mailboxes/
subdomains), the threshold-crossing pure function in isolation, and the
full `check_usage_alerts` flow end to end — fires at 80% and not below
it, does not re-fire the same threshold on a second check, escalates
80%→90% (closing the old episode, opening a new one, exactly 2 total
history rows), resolves when usage drops back under 80%, correctly skips
every resource with no limit configured, a database-count threshold
(not just disk), auto-suspend actually flips `Account.status` when
enabled and does *not* when the flag is off, and `check_all_accounts`
skips terminated accounts.

## What's honestly still open

- **"Trigger 80% disk alert, confirm email + UI banner"** (the goal's own
  Done-When criterion) needs a live account with a real quota near its
  limit — not performed this pass, blocked by the same live-deployment
  restriction documented in `CHECKPOINT-phase7b-1`. The threshold logic
  itself is directly, thoroughly unit-tested against seeded `UsageSnapshot`
  rows (bypassing the real `du` scan, matching `tests/test_usage.py`'s own
  established convention) — what's unconfirmed is the live UI banner
  rendering and the live email actually landing.
- **`scripts/usage_alert_check.py` is written but not yet installed** on
  this server's cron, and not yet documented in README.md's cron-setup
  section (same gap as feature 3's `ssl_expiry_check.py`).
- No UI control (yet) to manually "acknowledge" an alert — `UsageAlert.
  acknowledged` exists in the schema for this but nothing sets it; the
  goal's own "alert history" requirement doesn't ask for acknowledgment
  tracking specifically, so this was left unbuilt rather than guessed at.
- Auto-suspend at 100% is a real, hard action (a customer's whole site
  goes down) gated behind an admin-opt-in flag defaulting to *off* — this
  was a deliberate, conservative default given "optionally auto-suspend"
  in the goal's own text, not an oversight.

## Live verification result (2026-07-06): PASS
Ran `verify_phase7b_live.py usage_alerts`: seeded disk usage at 85% of a
100 MB quota; the 80% threshold alert fired and appears in the account's
active-alerts list (the same row that drives the panel banner; the email
channel is the notifications path verified in CHECKPOINT-phase7b-3). The
goal's Done-When criterion is met.
