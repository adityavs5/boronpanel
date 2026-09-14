# Boron — Architecture

Status: locked for v1. Decisions here are binding for all build phases; deviations
must be documented in CHECKPOINT.md with reasoning, not made silently.

This document assumes RESEARCH.md has been read. It does not re-justify claims
already sourced there — it states the resulting design and the engineering
tradeoffs specific to *this* implementation.

## 1. Stack summary

| Layer | Choice | Why (short) |
|---|---|---|
| OS | Ubuntu 24.04, single server | Locked by project goal |
| Web server | OpenLiteSpeed (free/OSS) | Locked; confirmed in RESEARCH.md §1 that v1 needs no commercial feature |
| PHP | LSAPI via `lsphp`, versions 8.1 + 8.3 | suEXEC-equivalent per-account isolation, free (RESEARCH.md §3) |
| Mail | Postfix + Dovecot, SQL-backed virtual mailboxes | Locked; SQL backend per RESEARCH.md §6 (ISPConfig pattern, not HestiaCP/Exim) |
| DNS | PowerDNS, **gsqlite3** backend, REST API only | Locked tool; backend+access-path per RESEARCH.md §6 |
| Hosted-account DB | MariaDB | Locked |
| Panel's own data | **SQLite** (control plane) + a dedicated MariaDB schema (mail only) | See §4 |
| FTP | Pure-FTPd, `-l unix`, chroot to home | RESEARCH.md §4 |
| SSL | certbot, webroot + DNS-01 (PowerDNS API) challenge plugin, OLS reload deploy-hook | §8 |
| Backend | Python 3.12, FastAPI (API) + a separate root daemon (`borond`) | §2 |
| Panel DB layer | SQLAlchemy 2.0 + Alembic migrations | Mature, typed, works identically against SQLite and MySQL |
| Templating (configs + UI) | Jinja2 | One templating engine for vhost/postfix/dovecot config text *and* the server-rendered admin UI |
| Frontend | Server-rendered Jinja2 + htmx + vanilla CSS (vendored, no CDN) | "Basic admin UI" per scope; no SPA build pipeline to maintain for a single overnight build |
| Process supervision | systemd units | Standard, already the system init |

## 2. Privilege separation

Two long-running processes, enforced at the OS level (not just convention):

- **`borond`** (provisioning daemon) — runs as **root**, systemd service
  `boron-provisiond.service`. The *only* process allowed to: create/modify/
  delete Linux users, write OLS/Postfix/Dovecot/Pure-FTPd config, run
  `useradd`/`usermod`/`userdel`/`setquota`/`certbot`/`systemctl reload …`,
  open privileged ports, or hold MariaDB admin credentials. Listens **only**
  on a Unix domain socket at `/run/boron/provisiond.sock`, mode `0600`,
  owned by `root:boron-api` — actually mode `0660` group-readable by the
  `boron-api` group so the unprivileged API process can connect, but no
  other local user can. No TCP listener, ever.
- **`boron-api`** (REST API + admin/customer UI) — runs as unprivileged
  system user `boron`, systemd service `boron-api.service`. Owns all
  HTTP-facing concerns: TLS termination, auth/sessions, request validation,
  RBAC, request logging, and the Jinja2 UI. Holds **no** root capability and
  **no** direct MariaDB admin grant — for any privileged action (including
  `CREATE DATABASE`/`CREATE USER` for hosted accounts, which needs MariaDB
  admin rights) it sends an RPC request to `borond` over the Unix socket;
  `borond` is the only process holding the MariaDB admin credential.
  Listens on `0.0.0.0:9443` (TLS, own cert — see §8.4) — **corrected during
  Phase h**: this doc originally said `127.0.0.1:9443` "reachable
  externally", which is self-contradictory (a loopback-only bind is, by
  definition, not externally reachable). The panel has to actually be
  reachable from the operator's own browser to be useful, so it binds all
  interfaces like any other admin panel (cPanel/WHM's own ports work the
  same way); not proxied through OLS (avoids the circular dependency of the
  panel managing the OLS vhost that serves itself).

**RPC protocol** (`boron-api` → `borond`): length-prefixed JSON over
the Unix socket — 4-byte big-endian length, then a UTF-8 JSON object
`{"op": "...", "params": {...}, "request_id": "..."}`; daemon replies the same
framing with `{"ok": true, "result": {...}}` or `{"ok": false, "error":
{"code": "...", "message": "..."}}`. Deliberately not gRPC/HTTP-over-socket —
this is a closed, single-tenant, same-host link between two processes we
control on both ends; a custom minimal framing avoids pulling in a second RPC
stack for a problem this small, and is trivial to unit-test (it's just
`struct.pack`/`json.dumps`). Every RPC call is logged by the daemon to the
audit log (§9) with the caller's authenticated panel-user id (passed in
`params`, **not** trusted blindly — `boron-api` is unprivileged but the
daemon still treats it as the trust boundary for *system* actions; it does
not re-derive authorization, that already happened in the API layer before
the RPC was made. The Unix socket's filesystem permission is the actual
trust boundary between "can call borond at all" and "cannot"; per-user
RBAC happens one layer up, in `boron-api`, before an RPC is ever sent).

This satisfies the project requirement verbatim: *web UI not root; only
provisioning daemon needs root.*

## 3. Directory layout

```
/opt/boron/                     # application code (deployed, not user data)
  api/                               # FastAPI app
  daemon/                            # borond
  shared/                            # models, schemas, RPC protocol, used by both
  templates/                         # Jinja2: vhost.conf.j2, postfix maps, dovecot conf, UI pages
  static/                            # vendored CSS/JS (htmx, no CDN)
  alembic/                           # DB migrations (control-plane SQLite schema)
  scripts/                           # install.sh, e2e test scripts

/etc/boron/
  boron.toml                     # non-secret config (ports, paths, PHP versions enabled)
  secrets.env                        # 0600 root-only: MariaDB admin creds, PowerDNS API key, session secret key
  vhost-templates/                   # operator-overridable copies of the Jinja2 vhost templates

/var/lib/boron/
  boron.db                       # SQLite, control-plane (accounts, domains, users, sessions, api tokens, audit log)
  backups/<subsystem>/<timestamp>/   # pre-reload config backups (rollback source, §7)

/var/log/boron/
  api.log
  daemon.log                         # every privileged op, structured JSON lines

/run/boron/
  provisiond.sock                    # RPC socket (tmpfs, recreated by systemd on boot)

/home/<account>/                     # one Linux user per hosting account (useradd default base /home)
  public_html/                       # primary domain webroot
  <addon-domain>/                    # addon domain webroots, sibling dirs under the account home
  logs/                              # per-vhost access/error logs (OLS writes here)
  tmp/                               # PHP session/tmp dir, scoped per account (open_basedir boundary)
  .ssh/                              # present but account shell is /usr/sbin/nologin in v1 — no interactive SSH

/usr/local/lsws/conf/vhosts/<account>/vhconf.conf   # OLS's own convention, one vhost-config dir per account
/var/vmail/<domain>/<localpart>/     # Maildir mail storage, owned by shared `vmail` system user (uid/gid fixed, e.g. 5000)
/var/www/_suspended/index.html       # static page served to suspended accounts (§10)
```

Account home dirs stay under `/home` rather than a custom path: this is
deliberate compatibility with how the Linux quota subsystem, `chsh`/`chage`,
and Pure-FTPd's chroot-to-homedir default all expect things to look, and it's
what every reference panel in RESEARCH.md does.

## 4. Data layer: why two databases, not one

The locked stack says "panel's own data in PostgreSQL or SQLite." We chose
**SQLite** for the panel's control-plane metadata (accounts, domains, DNS
record cache/audit, mail domain/mailbox *records* the UI manages, sessions,
API tokens, audit log) — single file, zero extra service to run/secure/back
up, trivially backed up (`cp`), and the control-plane's write rate (admin
actions, not request-serving) never approaches SQLite's concurrency ceiling.
WAL mode is enabled so API reads don't block on daemon writes.

However, RESEARCH.md §6 found that Postfix's `proxy:mysql:` virtual-mailbox
map and Dovecot's SQL passdb/userdb are the proven, low-maintenance pattern —
and both need to speak **MySQL wire protocol** to a live database at mail
delivery/login time (this is a hard runtime dependency of Postfix/Dovecot
themselves, not a Boron design choice). Since MariaDB is *already* a
hard dependency for hosted-account databases, standing up a second database
engine (or Postfix/Dovecot's separately-packaged sqlite driver subpackages,
which we did not install) just for mail would add an extra moving part for
no benefit. **Decision: a dedicated `boron_mail` schema inside the same
MariaDB instance**, separate from both the panel's SQLite control plane and
from any hosted account's own databases. Two MariaDB users exist against it:
`boron_daemon` (full DML/DDL, credential held only by `borond`) and
`boron_mailro` (SELECT-only, credential embedded in Postfix's/Dovecot's
own config files, which already run as root/dovecot respectively — this is
the same exposure every SQL-backed mail setup in RESEARCH.md §6 accepts).
The `mail_domain`/`mail_user` rows are *also* mirrored into Boron's
SQLite control plane as read-mostly cache for the UI/API to query without a
MariaDB round trip — `borond` is the only writer to either side, in the
same transaction-ish sequence (write MariaDB row, then write SQLite row;
mail delivery only ever depends on the MariaDB side being correct, so a
crash between the two steps degrades the UI's cached view, never mail
delivery itself — an acceptable v1 tradeoff over a real distributed
transaction).

## 5. Account model

- **Linux username** = the account identifier everywhere (Linux user, OLS
  vhost name, FTP login, base for DB/mail naming). Chosen by the admin (or
  customer, if self-signup is ever enabled — not in v1) at creation time,
  validated against `^[a-z][a-z0-9]{0,15}$` (max 16 chars: matches cPanel's
  own historical limit and — concretely — leaves headroom under MySQL's
  64-char identifier cap for the `<user>_<suffix>` database/DB-user naming
  convention adopted from CyberPanel/ISPConfig, RESEARCH.md §5).
  Reject reserved/system names (allowlist check against `/etc/passwd`
  collisions and a static reserved-words list: `root`, `boron`, `vmail`,
  `mysql`, etc.) before ever shelling out to `useradd`.
- **Linux user creation**: `useradd --create-home --home-dir /home/<user>
  --shell /usr/sbin/nologin --comment "Boron account" <user>`, then a
  dedicated primary group of the same name (default `useradd` behavior on
  Ubuntu with `USERGROUPS_ENAB yes`). No interactive shell in v1 (SSH access
  is not in scope) — FTP/web/mail access only. Password set via `chpasswd`
  with a random initial value the admin must reset (or an admin-supplied one),
  hashed by the system's own `crypt()` via `chpasswd`, never handled in
  Python.
- **suspend** = `usermod -L <user>` (locks the password hash, blocks FTP/PAM
  auth) **+** swap the OLS vhost's context to serve the static suspended page
  for all paths (§10) **+** leave DNS and mail untouched (cPanel-equivalent
  semantics: a suspended account stops serving its site and stops
  FTP/login, but billing-relevant services like mail and DNS keep functioning
  unless the operator explicitly terminates).
- **unsuspend** = `usermod -U <user>` + restore the normal vhost context.
- **terminate** = full teardown in a fixed, idempotent order (each step
  individually safe to retry): disable+remove OLS vhost config → graceful
  OLS restart → drop MariaDB databases/users owned by the account → remove
  mail domains/mailboxes owned by the account (MariaDB rows + Maildir on
  disk) → remove DNS zones owned by the account via PowerDNS API → remove
  Pure-FTPd-relevant state (none beyond the Linux user itself, since we use
  `-l unix`) → `userdel --remove <user>` (also removes crontab/mail spool/
  home dir) → remove quota entries → write a final audit log row. Every step
  logs success/failure independently; a failed step halts the sequence and
  leaves the account in a `terminating` state surfaced in the UI rather than
  silently continuing (no orphaned-but-invisible resources — directly serves
  the "clean suspend/terminate, no orphaned configs/processes" requirement).
- **Quotas**: `setquota -u <user> <soft> <hard> 0 0 /` for block limits (ext4
  user quotas, enabled system-wide per §"Quota prerequisite" below); inode
  limits left at 0 (unlimited) in v1 — disk space is the operator-facing
  metric that matters for hosting plans, inode exhaustion is an edge case not
  worth UI surface in v1.

**Quota prerequisite (already applied to this server during the architecture
phase, not deferred to build)**: `/etc/fstab`'s root entry now carries
`usrquota,grpquota`, the filesystem was remounted live (`mount -o remount /`),
and `quotacheck`/`quotaon` have been run — `findmnt -no OPTIONS /` confirms
`quota,usrquota,grpquota` is active. This was done now because it requires a
remount (a system-level change best made once, deliberately, and verified)
rather than something the provisioning daemon should attempt lazily on first
use.

## 6. PHP / OLS vhost templating

One Jinja2 template (`templates/vhost.conf.j2`) renders an OLS vhost config
per account. Key fields, all driven by RESEARCH.md §3's conclusion (vhost-
level suEXEC over the shared `lsphp` binary, not a separate binary per
account):

```
docRoot                 /home/<user>/public_html
extprocessor <user>_php83 {
  type                  lsapi
  address               UDS:///run/boron/lsphp/<user>.sock
  maxConns              10
  env                   PHP_LSAPI_CHILDREN=10
  path                  /usr/local/lsws/lsphp83/bin/lsphp
  runOnStartUp          0
  extUser               <user>
  extGroup              <user>
  setUID                1
  initTimeout           60
}
scripthandler {
  add                   lsapi:<user>_php83 php
}
```

- Account's docroot and all files under `/home/<user>` are owned `<user>:<user>`.
  **Corrected during Phase b's real end-to-end testing** (the assumption
  below this paragraph was wrong and is kept here, struck through in spirit,
  specifically so the mistake doesn't get re-made): real-world testing on
  this server found that "DocRoot UID" (`setUIDMode 2`) only governs the
  uid the LSAPI/PHP external app runs as (already covered by `extUser`/
  `extGroup` below) — it does **not** make OLS's main worker process switch
  uid per request for static-file serving or for locating the script to
  hand off to LSAPI in the first place. That worker keeps running as the
  server-wide `nobody` user the entire time. A mode-750 home dir (no
  "other" access) therefore produced a 403 on every request, because
  `nobody` couldn't even traverse into it. Making the docroot
  world-readable (755) to fix that was tried next and is the **wrong**
  fix — it lets every Linux account on the box `cat` every other account's
  files, confirmed by a real cross-account read test. **The actual fix**,
  the same pattern cPanel/DirectAdmin use: home dir mode `711` (owner full
  access, group/other execute-only — traversal without listing), docroot
  mode `750` (owner + private group only, no "other" access), plus a POSIX
  ACL granting the `nobody` user specifically `rX` (read, execute-if-dir),
  applied recursively with a default ACL so files created later (uploads,
  file manager, deploys) inherit it automatically. This passed both
  functional testing (page serves, HTTP 200) and isolation testing (a
  second unrelated Linux account gets `Permission denied`) on this VM.
- `extUser`/`extGroup` set to the account's own uid/gid in the LSAPI
  external app is the actual PHP-execution isolation mechanism
  (RESEARCH.md §3) — every `lsphp` worker for this account's vhost runs as
  that Linux user, confirmed by a live request whose PHP output reported
  its own `posix_geteuid()` identity back as the account's username, not
  `nobody` or `root`. Linux DAC permissions are the real enforcement
  boundary between accounts for PHP execution; the ACL above is the
  separate, necessary mechanism for the *static-serving* path, which runs
  under a different, shared identity.
- One `extprocessor` block is rendered **per (account × PHP version actually
  selected for that account)** — not one per installed PHP version
  server-wide. An account on PHP 8.3 gets exactly one `<user>_php83` external
  app; switching an account to 8.1 later re-renders the vhost with a
  `<user>_php81` block instead (old block removed, not left dangling).
- Per-vhost OLS native **cgroups v2** limits (CPU %, max tasks) are set from
  the account's plan/tier at render time — best-effort resource governance
  confirmed free in RESEARCH.md §3, scoped to PHP/CGI processes only (not
  cron/FTP, which are out of v1's resource-governance scope).
- Addon domains and subdomains render as additional `vhost` stanzas (OLS
  vhost-per-domain, not Apache-style `ServerAlias`) pointing at sibling
  webroots under the same account home, sharing the same `extprocessor`
  (same account = same PHP isolation boundary regardless of how many domains
  point at it).

**Reload**: `systemctl reload lshttpd` — confirmed by reading the installed
unit file (`/etc/systemd/system/lshttpd.service`) that `ExecReload=
/usr/local/lsws/bin/lswsctrl restart`, i.e. systemd's own `reload` verb is
wired to OLS's graceful restart. There is no separate "lighter" reload
primitive to discover; this *is* the graceful primitive (RESEARCH.md §2).

## 7. Validate → reload → verify → rollback (shared pattern, mandatory)

Every subsystem that involves writing a config file and reloading a service
goes through one shared sequence, implemented once in
`daemon/configtx.py` and reused by the OLS, Postfix, and Dovecot writers
(PowerDNS is API-based and gets its own equivalent in §"PowerDNS" below
since there's no local file to validate):

1. Render the new config to a temp file in the same directory as the target
   (so the final move is same-filesystem, hence atomic).
2. **Validate** before touching the live file:
   - OLS: `/usr/local/lsws/bin/openlitespeed -t` against the rendered config.
   - Postfix: `postfix check` + `postconf -n` against a `-c` pointed at a
     scratch copy of the config dir.
   - Dovecot: `doveconf -n -c <rendered file>`.
   - Any non-zero exit aborts here — the live config is untouched, the
     attempted change is logged as failed, the account/domain is left in its
     previous state (not a partial one).
3. **Backup**: copy the *current* live file to
   `/var/lib/boron/backups/<subsystem>/<timestamp>/<original-name>`
   before overwriting it.
4. **Apply**: `os.rename()` the validated temp file onto the live path
   (atomic on the same filesystem — no reader ever sees a half-written file).
5. **Reload**: the subsystem's reload command (§6 for OLS; `postfix reload`;
   `systemctl reload dovecot`).
6. **Verify**: confirm the reload actually took effect, not just that the
   command exit-coded zero (RESEARCH.md §2 explicitly flags OLS graceful
   restarts that report success but don't fully apply) —
   `systemctl is-active <service>` plus a service-specific liveness check
   (OLS: re-`openlitespeed -t` the *live* file and confirm the worker
   process start time advanced; Postfix/Dovecot: `systemctl show -p
   ActiveEnterTimestamp` advanced, or for a pure config-reload-no-restart
   case, that the process didn't crash-loop in the following 2s).
7. **Rollback on verify failure**: restore the backup from step 3, reload
   again, mark the operation `failed` in the audit log with both the
   attempted and restored config retained for operator inspection — never
   silently swallow a failed change.

This directly satisfies the project's non-optional requirement: *every phase
touching system config must validate before reload + rollback on failure.*

**PowerDNS** doesn't fit the file-based version of this pattern — there is no
local file, only REST calls (RESEARCH.md §6 decision). Its equivalent: zone/
rrset mutations are sent via `PATCH` with `changetype: REPLACE`; PowerDNS
itself validates and atomically applies or rejects the rrset (4xx response =
untouched zone, exactly the validate-before-apply property we want, just
provided by PowerDNS rather than by us). Boron's PowerDNS client checks
the response status and only writes the corresponding SQLite cache row after
a 2xx — so the "rollback" here is simply "never commit the local cache row
on a non-2xx," with no compensating action needed since PowerDNS's own write
was never partially applied in the first place.

**OLS-specific adaptation, discovered empirically while building Phase b**:
`openlitespeed -t` only ever validates the *live, installed* config tree —
passing `-c <path>` to point it at a candidate config is silently ignored.
That makes the generic "validate the temp file before touching the live
path" step impossible to do literally for OLS's two files (a per-account
`vhconf.conf` plus the shared, fully-regenerated `httpd_config.conf` — see
§6). The adaptation: `daemon/configtx.ConfigWriterMulti`'s `validate`
callable does only a cheap static pre-check (balanced braces, non-empty);
the real `openlitespeed -t` call happens as the *first action inside*
`reload()`, gated before the actual `systemctl reload lshttpd` is issued. A
real OLS validation failure is therefore reported as a reload failure,
which `ConfigWriterMulti` already knows how to roll back from — no special
casing needed, and "validate before reload" still holds in spirit: lshttpd
is never told to reload a config that hasn't passed `-t`, the gate just
moved from before the file write to before the service is told to apply it.
One consequence worth flagging explicitly: this server's stock OpenLiteSpeed
install bundles an "Example" vhost whose docroot is owned by root with a
uid/gid below OLS's own configured minimum (`CGIRLimit.minUID`/`minGID`),
which makes `openlitespeed -t` exit non-zero *even on an untouched, valid
install* — confirmed by testing the exact stock config. Boron's
regenerated `httpd_config.conf` never includes that vhost (it's rendered
from the DB's accounts/domains only), and a one-time `system.bootstrap_ols`
RPC op replaces the stock config with a clean, Example-free baseline before
the first real account is provisioned — without that, every rollback target
would itself look like a validation failure, since rollback restores
whatever config was live before the failed change.

## 8. SSL automation

- **certbot**, invoked by `borond` (root) via subprocess (RESEARCH.md
  confirms there's no mature library wrapping certbot well enough to prefer
  over the actual binary — every reference panel shells out to it too).
- **Challenge type, decided per-domain at issuance time**: if the domain's
  DNS zone is hosted in Boron's own PowerDNS, use the **DNS-01** challenge
  via certbot's `certbot-dns-...` PowerDNS-API plugin path (works even before
  the domain's A record points at this server, and avoids any port-80
  exposure requirement). If the zone is *not* Boron-managed (the operator
  pointed an externally-DNS-managed domain's A record at this server), fall
  back to **HTTP-01** via `--webroot` pointed at the account's
  `public_html/.well-known/acme-challenge/` — OLS already serves that path
  with zero extra vhost config since it's just a static file under the
  existing docroot.
- **Deploy hook**: `--deploy-hook` runs Boron's own small script that
  writes the new cert/key paths into the account's vhost config (TLS block)
  through the same §7 validate→reload→verify→rollback path, rather than
  trusting certbot's generic reload hooks — keeps the single shared
  reload-safety code path authoritative everywhere, including renewals
  triggered by certbot's own systemd timer outside of a Boron API call.
- **Renewal**: certbot's stock `certbot.timer` (installed with the package)
  handles renewal checks; Boron does not reimplement a renewal scheduler.
- **No real domain available for testing in this sandboxed build
  environment** (this VM has a public IP, 104.234.179.64, but no owned
  domain). **`sslip.io`** (`104-234-179-64.sslip.io` and subdomains
  thereof) is used for the v1 E2E SSL test — it's a real, public,
  third-party-operated wildcard-DNS-to-embedded-IP service requiring no
  registration, account, or payment, specifically built for exactly this
  "I need *a* real resolvable domain pointing at my box" scenario. This is
  the most conservative available option: it issues a genuine Let's Encrypt
  certificate via the real HTTP-01 path against the server's real public IP,
  which is the strongest validation achievable without registering a domain
  on the operator's behalf (an action this build deliberately does not take
  autonomously — domain registration costs money and creates an external
  account, outside the "non-destructive, reversible" bar for unattended
  actions).

## 9. Auth, RBAC, REST API, audit

- **Three roles**: `admin` (full access), `reseller` (only explicitly owned
  accounts), and `customer` (scoped to exactly one account — their own). The
  same login form and session mechanism serve all three; role and scope are
  resolved server-side from the authenticated identity and stored ownership,
  never trusted from client input.
- **Browser sessions**: signed, `httpOnly`, `Secure` cookies (Starlette
  `SessionMiddleware` with a server-held secret from `/etc/boron/secrets.env`),
  backed by a `sessions` table so sessions can be revoked server-side
  (logout-everywhere, admin-forced revocation) — a bare signed-cookie-only
  approach can't do that.
- **REST API auth (machine-to-machine, the "integration surface for a
  billing system" requirement)**: bearer tokens, format `fh_<role>_<32
  random bytes, base62>`, stored hashed (SHA-256) — never the raw token —
  with an admin-facing issue/revoke UI. Tokens are either administrator or
  single-customer scoped; reseller access is currently interactive-session
  only.
- **Every provisioning endpoint requires authentication, uniformly across
  HTTP verbs** — a direct, deliberate countermeasure to CyberPanel's
  CVE-2024-51567 root cause (RESEARCH.md §5: their input-sanitizing
  middleware only checked POST, so the identical payload via PUT bypassed
  it). Boron's auth/RBAC dependency is a FastAPI dependency injected on
  the router level, not per-handler, and applies identically regardless of
  verb.
- **No shell command is ever built by string-concatenating request input.**
  All `borond` subprocess calls use argument-list `subprocess.run([...],
  shell=False)`, never `shell=True`, never an f-string building a command
  line. Any value that must appear in a shell-adjacent context the daemon
  itself controls (e.g. a generated config file) goes through Jinja2
  autoescaping or explicit allowlist validation (the username regex from §5,
  domain-name validation via Python's own `idna`/`encode` round-trip) before
  it's ever interpolated anywhere — also a direct response to
  CVE-2024-51567/51568.
- **Audit log**: every privileged RPC (`borond` side) and every
  state-changing API call (`boron-api` side) writes one row — actor,
  role, target account/domain, operation, params (secrets redacted),
  result, timestamp — to SQLite. This is the operator-facing trail a
  professional hosting admin expects (matches cPanel/WHM's own audit log
  concept) and is also what makes "what was built, what's untested" in
  STATUS.md verifiable after the fact rather than asserted.

## 10. Suspended-account page & file manager scoping

- Suspension swaps the account's vhost `docRoot`-equivalent context (via a
  context-level rewrite-all rule, not a docroot swap, so the original files
  stay untouched and ownership/permissions never change) to serve
  `/var/www/_suspended/index.html` for every request, instead of removing or
  disabling the vhost outright — this is what keeps the *unsuspend* path a
  pure metadata flip rather than a config regeneration from scratch.
- The **file manager** (Phase g) is a `boron-api` feature, not a
  `borond` one — it operates as the unprivileged `boron` user but
  must still read/write files owned by arbitrary account uids. Two options
  considered: (a) run file-manager file I/O through `borond` (root) via
  RPC, or (b) grant `boron-api`'s OS user supplementary ACL access.
  **Decision: (a), through `borond`** — keeps the "only the daemon
  touches account-owned files" invariant absolute (no POSIX ACL surface to
  audit separately), and every file-manager action becomes an audited RPC
  call for free. All paths are resolved with `os.path.realpath` and checked
  to remain within `/home/<account>/` (or the request is rejected) before
  any read/write/delete — the explicit jail the project scope requires —
  applied in the daemon, the actually-privileged process, not just in the
  API layer (defense in depth: even if an API-layer check were buggy, the
  daemon re-validates independently before touching disk).

## 10.5 ModSecurity/WAF (Phase 5 feature 7) — available, but server-wide only

**Availability was checked empirically, not assumed.** This OLS build's
`openlitespeed -v` banner advertises `mod_security 1.4 (with
libmodsecurity v3.0.14)` as compiled in, but the loadable
`mod_security.so` was not actually present in `/usr/local/lsws/modules/`
on a fresh install (confirmed by a real `openlitespeed -t` failure —
"cannot open shared object file" — before this feature assumed it was
usable), and no OWASP Core Rule Set was installed anywhere either.
**Fixed with two official packages**, not a third-party/arbitrary
source: LiteSpeed's own `ols-modsecurity` (same `rpms.litespeedtech.com`
repo `openlitespeed` itself already comes from) for the module, and
Ubuntu's official `modsecurity-crs` (universe repo) for the ruleset.
Confirmed live end-to-end afterward: a real reflected-XSS probe and a
real SQLi probe against a live vhost both returned `403` (OWASP CRS
rules firing exactly as documented), with ordinary traffic to the same
vhost unaffected (still `200`) — see `docs/CHECKPOINT-phase5-7-waf.md`
for the full transcript.

**Real, load-bearing scope limitation, also confirmed live**:
OpenLiteSpeed has **no per-virtual-host ModSecurity configuration at
all** — the engine and its rule files load exactly once, server-wide,
in a single `module mod_security {}` block in `httpd_config.conf`. A
`modsecurity {}` block placed at the vhost-config level is rejected
outright by `openlitespeed -t` as an unrecognized keyword (confirmed
directly, not just taken from OpenLiteSpeed's own forum, which
independently says the same thing: "I don't think there's a way to
apply modsecurity by user/virtual host on OpenLiteSpeed"). Consequently,
this project's "per-domain WAF enable/disable" and "custom rules per
domain" are implemented as ModSecurity rule-language conditionals keyed
off the `Host` request header (`SecRule REQUEST_HEADERS:Host "@streq
<domain>" ...`, with `ctl:ruleEngine=Off` for a disable and a chained
match rule for a custom block) rather than as genuinely separate
per-vhost engine instances — the engine itself remains one global
on/off switch (`daemon/waf.py`, `WafSettings`); only individual *rules*
can be scoped to a single domain. Any future Boron feature that
assumes per-vhost module-level control over ModSecurity on OpenLiteSpeed
specifically should re-read this section first — it's a real constraint
of the web server, not a Boron design choice.

## 11. Explicit scope boundaries

Boron remains a single-server panel. It does not include multi-server/WHM
orchestration, reseller billing, bundled webmail, a plugin marketplace,
payment/invoicing, a mobile app, or features that require a commercially
licensed LSWS capability. Reseller account management, portable backup/restore,
cron management, and cPanel/DirectAdmin imports were added by later expansion
batches; their current boundaries are documented in
`EXPANSION-IMPLEMENTATION-PLAN.md` and the corresponding checkpoint files.

## 12. Testing strategy

`pytest` against the pure-logic surfaces that don't require root or a live
service: username/domain validation, Jinja2 template rendering (snapshot the
rendered vhost/postfix/dovecot config text for known inputs), the §7
validate→reload→verify→rollback state machine (with the actual
`openlitespeed -t`/`postfix check`/etc. calls mocked via
`unittest.mock.patch` on `subprocess.run`, asserting it's called with the
correct argument *list*, never a shell string), and the RPC framing
(`struct.pack`/`json.dumps` round-trip). Real system mutation (actually
running `useradd`, actually reloading OLS) is exercised only by the explicit
E2E script in `scripts/e2e_test.sh` run against this live VM, never by the
unit test suite — keeps `pytest` runnable in CI/containers with no root and
no real OLS/Postfix install present.
