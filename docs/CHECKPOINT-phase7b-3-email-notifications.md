# Checkpoint: Phase 7b Feature 3 — Email notifications

## What was built

Transactional emails for account created/suspended/unsuspended/
terminated, backup completed/failed (full backups only), SSL cert
expiring (14-day window), usage-limit-reached, and new customer-panel
login — relayed via this server's own local Postfix through stdlib
`smtplib` (no new third-party mail library, matching this project's
existing "stdlib/real system tool over a new dependency" posture).

- `daemon/events.py`: the single fan-out point every lifecycle-event
  source (below) calls into once, which then independently invokes both
  `daemon/notifications.py` (this feature) and `daemon/webhooks.py`
  (feature 4) — each channel does its own enabled/subscribed filtering,
  and one channel's failure is caught and logged without ever blocking
  the other or the real action that triggered the event.
- `NotificationSettings` (single-row, admin sender address + global
  per-event-type on/off) and `AccountNotificationPrefs` (per-account
  customer email + per-event opt-out, lazily created on first read/write)
  — a send requires **both** to allow the event, plus a customer email
  actually being set.
- Wired at: `handlers_account.create_account`/`reactivate_account`
  (CREATE_HOOKS, `account.created` — carries the plaintext initial
  password via a transient, non-persisted attribute stashed on the
  Account row right before hooks run, since it exists only in that
  function's local scope), `SUSPEND_HOOKS`/`UNSUSPEND_HOOKS`/
  `TERMINATE_HOOKS` (account lifecycle), `daemon/backup.py`'s
  `_run_backup_job` (full backups only — a granular file/DB/mailbox
  backup is routine and this project's own goal text doesn't ask to
  notify on it), a new `scripts/ssl_expiry_check.py` cron calling
  `ssl.check_expiring_certificates()` (dedup via a new `SslExpiryNotice`
  table keyed on `(domain, expiry_date)`, so a renewed cert with a new
  expiry date correctly re-notifies rather than being silently
  suppressed forever), and `handlers_auth.create_session` (customer role
  only — an admin login has no `AccountNotificationPrefs` row to check
  against, since that table is keyed by account, not panel user).
- **API**: `GET`/`PATCH /api/v1/admin/notifications/settings`,
  `GET`/`PATCH /api/v1/accounts/{u}/notification-prefs` (matches the
  goal's literal spec).
- **UI**: admin settings page (sender + per-event checkboxes), per-account
  prefs page (customer email + per-event checkboxes), both linked from
  nav/account-detail respectively.

## A significant real bug found and fixed by adversarial review

Found and confirmed live against an isolated temp-file SQLite DB (not the
shared production one) during the same second, adversarial review pass
described in `CHECKPOINT-phase7b-1`'s methodology note: both
`set_settings` and `set_prefs` **unconditionally overwrote**
`sender_address`/`customer_email` on every call
(`params.get("sender_address", "")` / `params.get("customer_email")`,
neither gated on whether the caller actually provided that field). A
caller updating *only* the per-event checkboxes — exactly what the UI's
own settings/prefs forms do when an admin/customer just flips one event
toggle — would silently wipe `sender_address` to `""`, which this
module's own `maybe_send` treats as "notifications globally disabled" —
turning off every notification for every account with no visible error,
until someone noticed emails had stopped. The equivalent per-account bug
would silently clear a customer's saved notification email. Reproduced
directly (`set_settings({"sender_address": "..."})` then
`set_settings({"events": {...}})`, confirming the address came back
empty) before fixing. Fixed with `"sender_address" in params`/
`"customer_email" in params` (not `.get(key, default)`) — the same
partial-update convention `handlers_account.set_limits`'s own
`params.get("cpu_pct", account.cpu_pct)` pattern already establishes
elsewhere in this project, adapted since `""`/`None` are meaningfully
different from "the caller didn't send this field at all." The API
routers' own `model_dump()` calls were also switched to
`exclude_unset=True` so a genuinely partial REST `PATCH` (as opposed to
the UI's always-resend-the-whole-form POST) gets the same protection.

## Tests

60+ new tests across `tests/test_notifications.py`,
`tests/test_events.py`, plus additions to `tests/test_handlers_account.py`
(the `initial_password` stash), `tests/test_handlers_auth.py` (the login
hook, admin-excluded), `tests/test_backup.py` (completed/failed events,
granular-kind exclusion), and `tests/test_ssl.py` (the expiry-check
scan: fires within the 14-day window, skips a cert with 60 days left,
dedups on re-run, re-fires after a simulated renewal with a new expiry
date). `daemon/notifications.py`'s own `_send_email` is mocked in every
test (no real SMTP dependency for the unit suite, matching this project's
established testing philosophy); `events.emit`'s own failure-isolation
(one channel raising must not block the other, must never propagate to
the caller) is tested directly.

## What's honestly still open

- **"Account created email received end-to-end"** (the goal's own Done-
  When criterion) requires a live send through the real local Postfix
  relay to an actual mailbox — not performed this pass, blocked by the
  same live-deployment restriction documented in `CHECKPOINT-phase7b-1`
  (a second, concurrently active Claude Code session on this same live
  server). The SMTP call itself (`smtplib.SMTP("localhost", 25)`) is a
  plain, well-understood stdlib call against a Postfix instance already
  proven live and working for hosted mail since Phase e — the only
  genuinely untested part is this feature's own new code path invoking
  it, not the underlying mail infrastructure.
- **SSL-expiry cron is written but not yet installed** on this server's
  `/etc/cron.d/` (matching `usage_snapshot.py`/`backup_scheduler.py`'s own
  documented-but-operator-installed pattern) — needs the identical
  `/etc/cron.d/forgehost-ssl-expiry` entry documented in this project's
  README, not yet added to it this pass (see "What to review first"
  below).
- Only a single sender address is supported (one `NotificationSettings`
  row) — no per-account custom "From" address, which the goal doesn't ask
  for either.
- `login.new` fires on every new session, including a customer with an
  active "remember me"-style long session logging in again shortly after
  expiry — no rate-limiting/dedup on this event type specifically (unlike
  `ssl.expiring`'s explicit dedup table), since a login is a legitimately
  repeatable real-world event, not a persistent state crossing a
  threshold once.

## What to review first on wake-up

1. Add the `/etc/cron.d/forgehost-ssl-expiry` entry to `README.md` (same
   pattern as the existing `forgehost-usage`/`forgehost-backups` entries)
   and actually install it on this server once only one Claude Code
   session is confirmed active.
2. Run the real end-to-end "create an account, confirm the email arrives"
   check the goal's Done-When section asks for, once live deploy is
   unblocked.

## Live verification result (2026-07-06): PASS
Ran `verify_phase7b_live.py notifications`: `maybe_send` returned True and
the message appeared in the account's real Maildir, delivered end-to-end
through this server's Postfix/Dovecot. The goal's Done-When criterion
("account created email received end-to-end") is met.
