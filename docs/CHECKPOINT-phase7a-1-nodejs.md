# Phase 7a Feature 1: NodeJS app hosting

## What was built

Per-account NodeJS app hosting via real systemd services
(`forgehost-node-{username}-{id}.service`), never running as root:

- **Runtime**: Node 18.20.8 / 20.20.2 / 22.23.1 installed side by side under
  `/opt/forgehost-nodejs/<major>/` (official prebuilt linux-x64 tarballs from
  nodejs.org — the same "several full runtimes side by side, chosen per
  account/app" pattern this project already uses for PHP via `lsphpNN`).
  Deliberately **not** `/opt/forgehost/nodejs` (see "real bugs found" below).
- **Data model**: `NodeApp` (`shared/models.py`) — one row per app, 1:1 bound
  to one of the account's own domains, `port` (allocated from a shared
  Node+Python range via `daemon/portalloc.py`), `node_version`, `env_vars`
  (Fernet-encrypted at rest, `daemon/appcrypto.py`), `enabled` (desired
  running state, persists across reboot).
- **Daemon**: `daemon/nodeapps.py` — create/update/delete/start/stop/restart/
  npm_install/get/list/logs, all as RPC ops (`apps.node.*`). Shared systemd
  plumbing factored into `daemon/appunits.py` (unit naming, env-file writing,
  start/stop/enable/disable, log-file tailing) — reused as-is by
  `daemon/pythonapps.py` (feature 2) and `daemon/redisacct.py` (feature 3).
- **Isolation**: unit runs as `User=<username>` (never root), `Slice=
  forgehost-<username>.slice` (the account's existing cgroup slice,
  `daemon/cgroups.py`, assigned directly at spawn time — no periodic
  reconciler needed the way LSAPI PHP workers require, since a
  systemd-spawned unit can be told its target slice up front).
  `Restart=on-failure` / `RestartSec=2` for crash recovery.
  `StandardOutput`/`StandardError` append to `~/logs/node/<name>.log` (a real
  file, never the system journal, per the goal's explicit requirement).
- **Reverse proxy**: OLS's native "Web Server (Proxy)" external app
  (`extProcessor ... { type proxy }`, `templates/httpd_config.conf.j2`) +
  a `Proxy Context` (`type proxy; handler <name>`, `templates/vhost.conf.j2`)
  replacing that domain's normal PHP context entirely — an app-bound domain's
  vhost context `/` becomes a pure reverse proxy to `127.0.0.1:<port>`.
  `daemon/ols.py`'s `_app_proxy_map`/`_all_active_vhosts`/`_domains_as_plain`
  were extended to carry this per-domain, backward-compatible (existing
  tests/call sites that never pass `app_proxy` render identically to before).
  Suspending an account still always wins — a suspended app-backed domain
  shows the same static suspended page as a PHP one, never a stopped/
  crashing proxy target.
- **env vars**: encrypted at rest (`cryptography.fernet`, key auto-generated
  once into `/etc/forgehost/secrets.env`, root-only — the same "forgehostd is
  the only reader" convention every other secret in that file already
  follows). Decrypted only into a **root-only** (0600) systemd
  `EnvironmentFile`, which systemd itself reads before dropping privilege to
  the account's own uid — a decrypted value is never written anywhere the
  hosting account's own uid can read it.
- **API/UI**: `/api/v1/accounts/{u}/apps/node` (+ `/{id}/{start,stop,restart,
  npm-install,logs}`), server-rendered pages at `/ui/accounts/{u}/apps/node`
  (list+create) and `/ui/accounts/{u}/apps/node/{id}` (detail: lifecycle
  buttons, config form, last 100 log lines).
- **Account termination**: `TERMINATE_HOOKS` entry removes every NodeApp's
  systemd unit + env file + DB row (idempotent). App code under
  `~/nodeapps/<name>/` is deliberately left on disk on both `delete()` and
  termination — same "routing removal, not content destruction" convention
  `handlers_domain.remove_domain` already established.

## Real bugs found and fixed during this feature's own build/verification

1. **Node runtime install path collided with the rsync-deploy target.**
   First attempt installed Node under `/opt/forgehost/nodejs` — but
   `scripts/deploy.sh` `rsync --delete`s the git checkout onto
   `/opt/forgehost`, and `nodejs/` isn't part of the source repo, so the
   very next deploy would have silently deleted every installed Node
   runtime. Caught before it happened; moved to a sibling directory
   (`/opt/forgehost-nodejs`) outside the deployment tree.
2. **OLS extProcessor `type web` doesn't exist.** The admin-console label is
   "Web Server (Proxy)", but the real accepted raw-config keyword (confirmed
   via `openlitespeed -t`'s own error — `Unknown external processor <type>:
   web` — and independently via `strings` on the `openlitespeed` binary,
   which lists `proxy` as an accepted external-processor type and nothing
   named `web`) is `type proxy`, matching the Proxy Context's own `type
   proxy` value. Fixed in `templates/httpd_config.conf.j2`.
3. **A failed `ols.refresh_vhost()` left an orphaned `NodeApp` DB row +
   systemd unit.** Found live: the bug above (#2) failed the very first real
   `apps.node.create` call *after* the DB row was already committed (needed
   for `allocate_port`/name-uniqueness to see it). With no compensation,
   every subsequent `create()` for the same domain/name failed with "already
   has an app bound to it" — a permanently broken domain until manually
   cleaned up. Fixed by wrapping the filesystem/unit/OLS-apply steps in a
   `try/except` that deletes the DB row and removes the unit on any failure
   (the same compensation discipline `handlers_domain.add_domain` already
   established for the identical failure shape). Identical fix applied to
   `daemon/pythonapps.py` (feature 2) preemptively, before it could be hit
   there too.
4. **`enabled=True` at `create()` time was semantically wrong.** `create()`
   deliberately does not start the systemd unit (the customer's code likely
   isn't deployed/npm-installed yet), but the DB flag defaulted to `True`
   anyway — so `update_app()`'s "only restart if currently enabled" guard
   would restart an app that was never actually started. Fixed to default
   `enabled=False`; `start_app()` is what flips it (and actually
   enables+starts the unit).
5. **`daemon/procutil.py`'s `run()` had no `cwd` support**, needed for
   `npm install`/`pip install`/`python3 -m venv` to execute inside the app's
   own directory (as the account's own uid via `runuser`, which — confirmed
   empirically — preserves the *caller's* cwd rather than the target user's
   home). Added as an optional keyword-only parameter, fully backward
   compatible.

## Live verification (real, on this VM — not a mock)

Disposable test account `p7anodetest` (terminated after verification):

- Real Express app (`package.json` declaring `express`, a plain `server.js`)
  written to `~/nodeapps/myapp/`, dependencies installed via the real
  `apps.node.npm_install` action (`npm install`, 68 packages, 0
  vulnerabilities), started via `apps.node.start`.
- `systemctl status` confirms the process's own cgroup:
  `/forgehost.slice/forgehost-p7anodetest.slice/forgehost-node-p7anodetest-1.service`
  — real cgroup assignment, not asserted, observed.
- `curl` against the real public domain
  (`p7anodetest.104-234-179-64.sslip.io`, both plain HTTP and HTTPS,
  resolved via real public DNS to this server's real IP) returned the
  Express app's real JSON response, including its own env var
  (`GREETING=hello-from-forgehost-phase7a`) round-tripped correctly through
  the encrypted-at-rest storage.
- `apps.node.logs` returned the real `console.log` line
  (`listening on 127.0.0.1:30000`) from the app's own log **file**
  (`~/logs/node/myapp.log`), confirmed it is not journald-backed.
- `account.terminate` confirmed to remove the systemd unit, env file, DB
  row, cgroup slice, and Linux user/home dir — nothing orphaned.

## What's untested / deferred

- Only Node's `npm`-based dependency install path was exercised (no `yarn`/
  `pnpm` support — not requested by the goal).
- The first systemd start of the very first NodeJS app on this server hit a
  transient `219/CGROUP` exit twice before succeeding on the third attempt
  (`journalctl`), self-healed by `Restart=on-failure`/`RestartSec=2` within
  ~4 seconds with no further recurrence across every subsequent app started
  in this same verification session (Python app, and re-tests). Root cause
  not conclusively identified (a systemd/cgroup-delegation race immediately
  after a brand-new unit file's first `daemon-reload`+`start`, on this
  particular sandboxed kernel/systemd version) — documented rather than
  silently ignored, since the crash-restart safety net the goal itself
  requires is exactly what absorbed it.
- Multiple concurrent NodeJS apps under one account were not stress-tested
  against the account's own cgroup memory/CPU limits (only the pre-existing
  Phase 2 feature 6 cgroups mechanism itself was stress-tested, in Phase 2).
