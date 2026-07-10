# CHECKPOINT run-a-5 — Health monitoring alerts

**Goal:** daemon checks every 5min: OLS, Postfix, Dovecot, PowerDNS,
MariaDB, Pure-FTPd, fail2ban. Email admin on down + recovery. 30min
cooldown per service. Admin UI: 24h uptime history, last alert sent.
API: `GET/PATCH /admin/monitoring/settings`, `GET /admin/monitoring/history`.

## What was built

- **Models**: `MonitoringSettings` (single row: enabled, admin_email,
  cooldown_minutes default 30 — the alert *sender* deliberately reuses
  `NotificationSettings.sender_address`, one outbound identity, not two
  that drift), `ServiceCheck` (one row per service per pass, 48h
  retention pruned by the pass itself — same reasoning as
  HealthSnapshot), `MonitoringAlertState` (per-service: is_down,
  down_since, last_alert_sent_at).
- **Daemon** `daemon/monitoring.py`: `MONITORED_SERVICES` =
  `servicemgr.SERVICE_REGISTRY` (the module that already owns real unit
  names, incl. the `postfix@-.service` gotcha) **+ fail2ban** (monitored
  but deliberately not admin-stoppable via servicemgr). `check_services()`
  runs `systemctl is-active` per unit, writes history, and handles
  transitions:
  - up→down: alert email (subject `[monitoring] X is DOWN`).
  - still down: re-alert only after the cooldown.
  - down→up: recovery email with the downtime duration, always attempted
    (the "it's fixed" signal is what the admin is waiting for).
  - **`last_alert_sent_at` advances only on a successful SMTP handoff** —
    load-bearing for the Postfix-is-the-down-service case: the down
    alert's send fails (the MTA is the thing that died), is retried each
    pass, and the recovery email after Postfix returns is what actually
    reaches the admin, carrying the downtime window.
  - Ops: `monitoring.settings.get/set`, `monitoring.history`,
    `monitoring.check` (the latter two in `REPORTING_OPS`).
- **Cron**: `scripts/monitoring_check.py` (+x) + `deploy/
  forgehost-monitoring.cron` (`*/5`, same install convention as
  forgehost-cloudflare.cron). Not installed to /etc/cron.d here — that's
  a deploy step (and feature 9's installer).
- **API** `api/routers/monitoring.py`: GET/PATCH
  `/api/v1/admin/monitoring/settings`, GET
  `/api/v1/admin/monitoring/history?hours=24` — all `require_admin`.
- **Frontend**: "Service monitoring" card on the admin Server Health page
  — settings row (alert email, cooldown, enable toggle) + per-service 24h
  rows: up/down badge (with down-since), inline-SVG uptime step-sparkline,
  uptime %, last-alert timestamp.

## Verified live

Against the **real systemd + real mail stack** (scratch DB/config only —
the code under test is the repo's real module):
- Baseline pass: all 7 production services detected `active`.
- The goal's literal "stop Postfix" was **blocked by the permission
  classifier** (production service disruption needs operator approval) —
  run-book equivalent for the operator, post-deploy:
  `systemctl stop postfix@-.service && /opt/forgehost/scripts/monitoring_check.py; systemctl start postfix@-.service && /opt/forgehost/scripts/monitoring_check.py`
  → expect the recovery email (down-alert send fails while the MTA is
  down, by design — see above).
- Identical mechanics verified with a **disposable transient unit**
  (`fh-monitor-selftest.service` via systemd-run, added to
  MONITORED_SERVICES for the scratch passes only):
  - stop → pass → `selftest:down` alert sent → **real delivery
    confirmed** through Postfix → SpamAssassin → Dovecot LMTP →
    `stored mail into mailbox 'INBOX'` (mail.log) into the Phase 7b
    verification mailbox; Maildir count 1→2.
  - immediate re-pass while still down → **nothing sent** (cooldown
    verified live, not just in tests).
  - start → pass → `selftest:recovery` sent and delivered (Maildir 2→3).
  - Unit removed after (transient; confirmed inactive).

## Tests

`tests/test_monitoring.py` — 14 tests: settings defaults/partial
update/validation bounds; all-up pass writes history + no alerts; down
transition alerts with correct recipient; cooldown suppresses (and
expiry re-alerts with "still DOWN"); recovery email with duration +
state reset; **failed send doesn't consume cooldown** (retries next
pass); **recovery-after-failed-down-alert still delivers** (the Postfix
case); disabled monitoring records history but never emails; missing
admin_email never sends; history uptime % + series + all-7 coverage;
48h retention pruning.

## What's honestly still open

- The literal stop-Postfix Done-When needs the operator run-book line
  above (blocked on production-disruption approval; mechanics fully
  verified otherwise).
- Cron not installed on this box yet (deploy step; the installer in
  feature 9 will install it on fresh systems).
