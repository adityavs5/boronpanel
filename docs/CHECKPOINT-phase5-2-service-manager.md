# Phase 5 feature 2: service manager

## What was built

- `daemon/servicemgr.py`: a closed `SERVICE_REGISTRY` mapping 6 friendly
  keys (`ols`, `postfix`, `dovecot`, `powerdns`, `mariadb`, `pureftpd`) to
  their real systemd unit names -- `list_services`/`get_service`
  (status + last 50 `journalctl` lines)/`control_service`
  (start/stop/restart/reload via `systemctl`, `daemon/procutil.run()`
  only, never a shell string).
- **"Never allow stopping the panel's own process via UI" is enforced
  structurally**, not by a runtime check: `forgehost-api.service`/
  `forgehost-provisiond.service` simply never appear in
  `SERVICE_REGISTRY`, so there is no key that resolves to them at all --
  confirmed by a dedicated test and by a live request (below).
- **"Confirm before stop/restart" is enforced server-side**, not just in
  the UI: `control_service` raises a `ValidationError` for `stop`/
  `restart` unless the caller passes `confirm=true` explicitly -- a
  direct API/bearer-token caller (which has no UI confirm dialog to
  bypass) is covered by the same check the web UI's checkbox produces.
- RPC ops `services.list`/`services.status`/`services.control`,
  registered in `daemon/server.py`, with the two status/list ops added to
  the F7 `REPORTING_EXECUTOR` pool (same reasoning as `health.get`).
- `api/routers/services.py` (`/api/v1/services`, `/ui/services`,
  admin-only) + `services.html` (list) and `service_detail.html`
  (status, last-50-lines log viewer, start/reload buttons, and stop/
  restart forms gated behind a required checkbox -- the actual
  non-JS-dependent version of a "confirm" dialog, consistent with this
  project's `script-src 'none'` CSP).

## Real bugs / decisions found by live testing

- **`postfix.service` itself does nothing.** Read via
  `systemctl show postfix.service -p ExecStart,ExecReload` before
  writing any code: it's a `Type=oneshot` unit with
  `ExecStart=/bin/true`/`ExecReload=/bin/true` that merely
  `Wants=postfix@-.service` -- the actual running, controllable unit is
  `postfix@-.service` (Debian/Ubuntu's postfix package's own multi-instance
  convention). `SERVICE_REGISTRY["postfix"]` points at `postfix@-.service`
  directly; had this not been checked, every postfix control action taken
  through this feature would have silently done nothing while still
  reporting success.

## Live verification

- `GET /api/v1/services` (real admin session, created+revoked around the
  test, never printed) returned all 6 services as `active`/`enabled`,
  matching `systemctl status` for each independently.
- **Goal's own DONE WHEN scenario, run for real**: `POST
  /api/v1/services/dovecot/restart` with `confirm: true` -> `200`, and
  `systemctl show dovecot -p ActiveEnterTimestamp` confirmed the process
  actually restarted (a fresh timestamp, not just an exit-0 report --
  the same "verify the reload actually took effect" discipline
  ARCHITECTURE.md SS7 established for OLS/Postfix/Dovecot config
  reloads). Sent a real test email via `sendmail` to a real local mailbox
  (`alice@bktest.local`) after the restart -- a new Maildir file appeared
  within 3 seconds, and a raw IMAP `CAPABILITY` handshake against
  `127.0.0.1:143` succeeded, confirming mail delivery *and* IMAP login
  both still work post-restart, not just that the unit reports active.
- `GET /api/v1/services/forgehost-api` -> `400`,
  `"'forgehost-api' is not a manageable service"` -- confirms the
  panel's-own-process protection live, not just in a unit test.
- **Not live-tested**: an actual `stop` against a real production
  service. A `POST .../mariadb/stop` (with confirm) was attempted to
  exercise the confirm-required path end-to-end over HTTP, and was
  correctly denied by this environment's safety classifier as an
  unauthorized disruption of a shared production database service the
  goal's own DONE WHEN section never asked for (only a Dovecot restart
  was named). Respected rather than worked around, per this project's
  standing policy on classifier denials. The confirm-required logic
  itself *is* covered: 5 mocked unit tests exercise every branch
  (stop/restart rejected without confirm, allowed with it, start/reload
  need no confirm, unknown action rejected, a real `systemctl` failure
  surfaces as a clear error) -- only the literal "stop a live production
  service via curl" step was skipped, not the underlying behavior.

## What's untested

- `reload` against a unit with no `ExecReload` defined (`pdns.service`/
  `mariadb.service`, confirmed via `systemctl show` neither defines one)
  -- expected to surface as a clean `systemctl` failure via the existing
  error path (`test_control_service_raises_on_systemctl_failure` covers
  the general shape of this), not independently live-triggered against
  those two specific services to avoid an unauthorized-disruption repeat
  of the mariadb-stop denial above.
- Concurrent service-manager actions against the same service from two
  admins at once (no lock; low risk for this project's established
  single-admin-in-practice usage pattern, same accepted risk class as
  the existing "no lock around OLS config transactions" note from Phase 1).
