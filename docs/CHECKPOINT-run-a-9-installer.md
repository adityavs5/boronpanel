# CHECKPOINT run-a-9 — Installer script

**Goal:** `scripts/install.sh` — one command, fresh Ubuntu 24.04.
Pre-flight (OS, RAM ≥1GB, disk ≥10GB, root, port conflicts). Install all
packages, configure each, deploy to `/opt/forgehost`, create systemd
services, run DB migrations, prompt admin credentials + panel domain + SSL,
configure UFW + fail2ban. `✓/✗` per step, log to
`/var/log/forgehost-install.log`. Idempotent, `--dry-run`, `--uninstall`.
shellcheck clean, no external deps beyond curl/bash.

## What was built

`scripts/install.sh` — a faithful, idempotent translation of README.md's
hand-tested runbook (the authoritative source: every command there was
actually run on this project's own server). Structure:

- **Pre-flight** (`preflight`): root (hard fail unless `--dry-run`), Ubuntu
  24.04 (warns on other Ubuntu, fails on non-Ubuntu), RAM ≥ 1024MB via
  `/proc/meminfo`, disk ≥ 10GB free on `/` via `df`, and port-conflict scan
  over the 11 ports the stack binds (`ss`). Conflicts are a warning, not a
  hard fail — expected when re-running over a partial install.
- **Packages**: base (python/venv/pip, node, npm, composer, git, ufw, acl,
  quota, sqlite3, jq, ca-certificates), the LiteSpeed repo + OpenLiteSpeed +
  lsphp 8.1/8.3, and the hosting stack (MariaDB, Postfix, Dovecot +
  dovecot-sieve, PowerDNS + sqlite3 backend, Pure-FTPd, certbot, rclone,
  SpamAssassin, fail2ban, redis-server). wp-cli is fetched as the official
  phar (curl only — it isn't in apt). Postfix is preseeded via debconf so it
  never shows its interactive config screen.
- **Quotas** (`setup_quota`): adds `usrquota,grpquota` to the `/` fstab
  entry, remounts, `quotacheck`/`quotaon` — skipped cleanly if already
  active.
- **System users**: `vmail` (fixed uid/gid 150 — a high uid would push every
  hosting account's uid up), `forgehost-api` (unprivileged). Both idempotent
  via `getent`.
- **Deploy** (`deploy_app`): rsync repo → `/opt/forgehost` (excluding
  `.git`/`.venv`/`node_modules`), create venv, pip install, build the SPA
  only if `static/dist` is missing (the repo ships it prebuilt). Creates
  `/var/log/forgehost` mode `2775 root:forgehost-api` so the unprivileged
  API can write its own access/error logs (Run A f7's one deployment
  prerequisite).
- **Secrets + config**: `forgehost.toml`, `api-secrets.env` (SESSION_SECRET),
  `secrets.env` (MariaDB root/daemon/mailro passwords, PowerDNS API key).
  Every secret is generated **only if absent** (`ensure_secret` /
  per-key `grep` guards) — a re-run never rotates a live secret.
- **MariaDB** (`setup_mariadb`): sets a root password, creates
  `forgehost_daemon` (explicit privilege list, not `ALL`) and
  `forgehost_mailro` (SELECT-only), the `forgehost_mail` schema + mailbox
  tables, and `/root/.my.cnf`. Guarded by a secret check so it runs once.
- **PowerDNS**, **SSL bootstrap** (self-signed default + panel certs +
  suspended page), **systemd units**, **cron + logrotate** (Run A f7 API log
  rotation, Run A f5 monitoring cron, Cloudflare crons, and the
  usage/backup/ssl-expiry/pma/usage-alert infrastructure crons),
  **bring-up** (restart provisiond → wait for its socket → `system.bootstrap_ols`
  → start api), **admin creation** (prompted, or `FH_ADMIN_PASSWORD`), and
  **UFW + fail2ban** (deny-incoming default, allow ssh/web/mail/DNS/FTP +
  the passive-FTP data range + the panel's 9443; fail2ban jails for
  ssh/postfix/dovecot/pure-ftpd).
- **DB migrations**: no separate step — the daemon's own `init_db()`
  (`create_all` + the additive-column migrations, incl. Run A f1's
  `plan_id`/`ftp_account_limit`/`app_limit`) runs on first provisiond start,
  which the bring-up step triggers.

## Flags + safety

- `--dry-run`: every mutating action prints `[dry] <command>` and changes
  nothing; read-only pre-flight checks still run for real and never abort
  (so a dry-run is runnable by any user on any box). Exits 0.
- `--uninstall`: removes services, `/opt/forgehost`, `/etc/forgehost`, and
  the installed cron/logrotate — **deliberately keeps** hosting data
  (`/home`, `/var/vmail`, MariaDB, the control-plane DB under
  `/var/lib/forgehost`) and prints exactly how to remove those too.
  Confirms interactively first.
- Non-interactive install via `FH_*` env vars
  (`FH_NONINTERACTIVE=1`, `FH_PANEL_DOMAIN`, `FH_LE_EMAIL`, `FH_ADMIN_USER`,
  `FH_ADMIN_PASSWORD`, `FH_SERVER_IP`).
- Every step prints `[ OK ]` / `[SKIP]` / `[FAIL]` and is appended to
  `/var/log/forgehost-install.log`; a running `Summary: N ok, M skipped, K
  failed` is printed at the end and a non-zero failure count exits non-zero.

## Verification

- **shellcheck clean** (v0.9.0, zero findings) — the goal's hard gate.
- **`--dry-run` exits 0** and prints the full plan; on this box (which
  already carries a dev install) idempotency correctly reports SKIP for
  already-present users/secrets/PowerDNS while still planning every
  install/config action.
- Pre-flight verified live: root ✓, Ubuntu 24.04 ✓, RAM/disk thresholds ✓,
  port scan lists the in-use ports as a warning.
- Not executed for real end-to-end here — a true install can only be
  validated on a genuinely fresh Ubuntu 24.04 box (it installs OLS, MariaDB,
  etc. system-wide and rewrites firewall/fstab). The script is a
  step-for-step transcription of the runbook that stood *this* server up,
  with idempotency guards and dry-run/uninstall added.

## What's honestly still open

- The literal "prompt for SSL" is handled as: the panel gets a self-signed
  cert at install (browser-trusted per-site certs are issued later through
  the panel's own `ssl.issue`); the installer collects the panel domain +
  LE email up front so an operator can issue a real panel cert immediately
  after. A fully automated "issue a real LE cert for the panel hostname
  during install" would require the panel's DNS/A-record to already resolve
  to the box at install time, which isn't guaranteed on a fresh install.
