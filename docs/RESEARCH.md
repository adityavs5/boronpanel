# Boron — Research

This document precedes ARCHITECTURE.md. It records what was learned from official
docs and prior-art codebases before any design decision was locked in. Every claim
below was verified against a primary source (vendor docs, source code, or release
notes) by dedicated research passes on 2026-06-30. Where a question turned out to
be unanswerable with confidence, that is stated explicitly rather than guessed.

## 1. OpenLiteSpeed (OLS) free vs. licensed boundary

**Bottom line: everything Boron v1 needs is free in OLS.** OLS is GPLv3,
unlimited domains/traffic, no license-server enforcement of any kind (confirmed by
reading `lswsctrl` itself — it only does PID/signal management, no license calls).
The commercial product is a *different binary* (LiteSpeed Web Server Enterprise),
not a paid unlock of OLS.

| Feature | Free in OLS? | Notes |
|---|---|---|
| Unlimited vhosts, unlimited workers | **Yes** | Enterprise is the one that's seat/traffic-metered, not OLS |
| Per-vhost SSL/SNI, rewrite rules, PHP External Apps | **Yes** | Not server-global-only |
| Multiple PHP versions side by side (`lsphp74`…`lsphp85`) | **Yes** | Installed as separate packages, selected per-vhost |
| **suEXEC / per-vhost `RunAsUser`-style PHP isolation** | **Yes** | Critical for Boron — see §3. Config keys `extUser`/`extGroup` (External App) and vhost "External App Set UID Mode" / "suEXEC User/Group" are present and functional in OLS; the official edition-comparison table checks "PHP/Ruby/CGI/FCGI/LSAPI suEXEC" for *both* editions. CyberPanel (free, OLS-only) ships this in production. |
| ModSecurity (OWASP CRS) | **Yes** | Open-source libmodsecurity v3 engine ships in OLS by default (`mod_security 1.4` confirmed present on this install). Enterprise swaps in a proprietary faster engine — functionally optional. |
| HTTP/3 / QUIC | **Yes** | lsquic is separately MIT-licensed |
| Rate limiting / per-IP throttling | **Yes** | Standard OLS feature |
| LSCache (page cache) | **Yes**, core caching | **ESI (Edge Side Includes) is Enterprise-only** — irrelevant to Boron v1 (no caching layer in scope) |
| `.htaccess` | Partial-free | Only `mod_rewrite` directives honored; requires a restart to pick up changes (no live reload). Full Apache-compatible `.htaccess` + live reload is Enterprise-only. Boron should prefer vhost-config-driven rewrites over relying on live `.htaccess` edits. |
| WebAdmin console (port 7080) | **Yes** | Ships by default; Boron does not depend on it but it remains available as an escape hatch for the operator |
| **REST/management API** | **No such thing in either edition** | Confirmed directly by LiteSpeed staff: there is no API, only WebAdmin (a GUI over the same config files) or hand-editing `httpd_config.conf` / `vhosts/<name>/vhconf.conf` + reload. This is *not* a paywall — it simply doesn't exist. Boron's provisioning daemon must template config files directly, exactly like CyberPanel does. |

No commercial LSWS feature is required anywhere in the v1 scope. The one feature
explicitly avoided is ESI-dependent cache plugins (not used — no caching layer in
v1).

## 2. Graceful reload / zero-downtime config changes

- `systemctl restart lsws` (equivalently `/usr/local/lsws/bin/lswsctrl restart`)
  performs a **graceful** restart: `SIGUSR1` to the master, new worker generation
  spawned, old generation drains in-flight requests before exit. This is the
  *only* reload primitive OLS exposes — there is no separate "graceful" vs "hard"
  command for our purposes; the default restart behavior already is graceful.
- `/usr/local/lsws/bin/openlitespeed -t` validates config syntax without applying
  it — **must** be run before every restart triggered by Boron. This is the
  validate-before-reload step the project goal requires.
- New vhosts and renewed/replaced SSL certs both require a graceful restart to be
  picked up — OLS does not hot-watch cert files. LiteSpeed staff's own recommended
  pattern is to chain `systemctl restart lsws` as a certbot `--deploy-hook`,
  which is exactly what Boron's SSL phase will do.
- Known upstream bugs exist where a graceful restart occasionally fails to fully
  apply (tracked GitHub issues). **Boron must not assume success** — after
  every restart it should verify the new config is actually live (e.g. re-fetch
  the vhost's listener / check process start time) rather than trust the exit
  code alone, and roll back the config file + retry/alert on mismatch.

## 3. PHP isolation pattern: one shared External App + vhost-level suEXEC

OLS runs PHP via `lsphp` (a dedicated CGI/LSAPI-speaking PHP build, not `mod_php`)
as an "External App" bound to vhosts through Script Handlers. Two isolation models
were compared:

- **(a) suEXEC via vhost-level `RunAsUser`/`RunAsGroup`** — a single External App
  definition per PHP version, with the *vhost* telling OLS which uid/gid to
  switch to (`extUser`/`extGroup`, "External App Set UID Mode" = DocRoot UID, or
  explicit suEXEC User/Group) before running PHP for that vhost.
- **(b) A dedicated External App per account** — a distinct LSAPI backend
  definition (own socket, own binary path) per account.

These are **not actually competing designs** — (b) only matters when accounts
need *different PHP versions or php.ini settings*, not for security isolation.
The uid switch in (a) already gives full Linux DAC isolation: the lsphp child
process genuinely runs as the account's own Linux user, so one compromised
account's PHP process cannot read another account's files, same guarantee as
cPanel's classic suEXEC/CageFS-adjacent model (minus CloudLinux's kernel-level
LVE containment, which doesn't exist outside CloudLinux's own kernel).

**Boron decision: one External App per (account × PHP version actually in
use)**, each with `extUser`/`extGroup` pinned to that account's Linux user. In
practice this means: define the small fixed set of installed PHP versions
(8.1, 8.3 chosen — see ARCHITECTURE.md) as base External App templates, and at
account-creation time render a per-account copy with `extUser`/`extGroup` set to
that account's uid/gid and the LSAPI socket path namespaced under
`/usr/local/lsws/run/lsphp/<account>.sock`. Default process mode is OLS's
"ProcessGroup" (the safe default) — one lsphp parent owned by the account's uid,
forking child workers under that same uid with their own opcode cache — *not*
"Daemon" mode, which would share one PHP pool across accounts and defeat
isolation. This is enforced by always setting suEXEC User/Group rather than
leaving it unset.

**Resource limiting**: free OLS has real per-vhost/per-External-App `rlimit`
fields (Memory Soft/Hard Limit, Process Soft/Hard Limit i.e. RLIMIT_NPROC,
PHP suEXEC Max Conn as a pool-size cap) and — corrected from an earlier
assumption — **free OLS also has native cgroups v2 support since v1.8**
(`docs.openlitespeed.org/config/advanced/cgroups/`), capping CPU, virtual
memory, disk read/write bandwidth+IOPS, and max tasks, server- or vhost-wide,
keyed to the uid owning the script. Ubuntu 24.04's 6.8 kernel satisfies the
≥5.2 requirement. This is **not** CloudLinux LVE — it only governs
CGI/LSAPI-spawned processes (i.e. PHP), not the account's cron jobs, SSH
sessions, or FTP transfers. Boron v1 enables per-vhost cgroup limits for
PHP as a best-effort resource cap; if uniform whole-account governance
(cron + SSH + PHP together) is ever wanted, that would require wrapping the
account in a systemd slice — out of scope for v1, noted as a future option.

## 4. FTP server: Pure-FTPd (chosen) vs. vsftpd

Both can authenticate directly against real Linux accounts (no separate virtual-
user database needed, which matters because Boron accounts already exist as
system users from account creation): Pure-FTPd via `-l unix`/`-l pam` with
`ChrootEveryone yes`; vsftpd via PAM + `chroot_local_user=YES` and per-user
override files under `user_config_dir`.

Decision factors:
- **Maintenance**: Pure-FTPd has releases as recent as April 2026 and commits
  within the last two weeks of research. vsftpd's last upstream tarball is 3.0.5
  from 2021; its own author has said FTP is sunsetting and releases are
  deliberately infrequent — Ubuntu only ships patched rebuilds, not new upstream
  code.
- **Production precedent**: cPanel/WHM and DirectAdmin both default to
  Pure-FTPd; ISPConfig's standard install guides default to Pure-FTPd. HestiaCP
  is the outlier defaulting to vsftpd.
- **TLS config**: Pure-FTPd's single combined PEM cert config is simpler to
  template programmatically than vsftpd's multi-field cert config — a minor but
  real win for an automated provisioning daemon that has to write this file
  unattended.

**Decision: Pure-FTPd**, using `-l unix` (system account auth) + chroot-to-home,
no PureDB virtual users. This matches the project's existing account model
exactly (one Linux user per hosting account, created in Phase a) with zero
additional user database to keep in sync.

## 5. CyberPanel architecture (prior art — OLS-based panel)

CyberPanel (Python/Django) is the closest real-world analog: free, OLS-based,
one Django app per feature area (`websiteFunctions`, `databases`, `dns`,
`ftp`, `mailServer`, `manageSSL`, `managePHP`, `filemanager`, plus a shared
`plogical` utility layer). No async job queue (no Celery) — long operations use
polling endpoints and cron instead.

- **Account model**: one dedicated Linux system user per website (not shared),
  created via `adduser --no-create-home --home <path> --disabled-login`, with
  home layout `/home/<user>/public_html` + `/home/<user>/logs`. Matches the
  suEXEC-everywhere design above.
- **PHP isolation**: confirms §3 — `vhostConfs.py` templates a per-account
  `extprocessor` block (`type lsapi; address UDS://tmp/lshttpd/<user>.sock;
  extUser <user>; extGroup <user>; path .../lsphp<ver>/bin/lsphp`), i.e. exactly
  the "one LSAPI backend per account, uid-pinned" pattern Boron adopts.
- **Vhost templating/reload**: raw config text built via Python string templates,
  written straight to `/usr/local/lsws/conf/vhosts/<name>/vhost.conf`, applied
  via a full `lswsctrl restart` (not file-watch). Validates the "shell out to
  lswsctrl after writing config" pattern as the only real option (§2).
- **DNS**: PowerDNS with the `gmysql` backend, but CyberPanel reuses its own
  app database for it and writes PowerDNS's `domains`/`records` tables directly
  via Django ORM — i.e., direct SQL writes, not PowerDNS's own REST API. The
  PowerDNS HTTP API is enabled only for Let's Encrypt DNS-01 challenges. See §6
  for why Boron makes a different choice here.
- **Mail**: Postfix + Dovecot, MySQL-backed virtual mailboxes, single shared
  `vmail` system account (uid/gid 5000) owning Maildir storage at
  `/home/vmail/<domain>/<user>/`. Confirms the SQL-backed virtual-mail pattern
  in §6.
- **DB provisioning**: DB/user names prefixed with the web user
  (`<webuser>_<dbname>`), validated through an identifier-safety check, created
  via plain `CREATE DATABASE` / `CREATE USER ... IDENTIFIED BY` / `GRANT ALL ...
  TO 'user'@'localhost'`.

**What NOT to do — CyberPanel's CVE history**: CVE-2024-51567 (CVSS 10.0,
unauthenticated RCE) and CVE-2024-51568 were both classic shell-command string
concatenation from user input (`ProcessUtilities.outputExecutioner()` and an
unauthenticated `upgrademysqlstatus` endpoint), compounded by an auth/input
"sanitizer" middleware that only checked POST requests — the same payload sent
as PUT bypassed it entirely. CVE-2024-51378 was an authorization bypass in a
DNS/FTP status endpoint leading to root RCE. The October 2024 "PSAUX" ransomware
wave compromised ~22,000 exposed CyberPanel instances within hours of public
disclosure. **Boron takeaways, enforced in ARCHITECTURE.md**: never build
shell commands by string-concatenating user input — use `subprocess` with
argument lists (never `shell=True`) and validate/allowlist all identifiers that
touch a shell-out or SQL statement; apply auth/authz middleware uniformly across
all HTTP verbs, not just POST; default-deny on every provisioning endpoint;
never ship a static or predictable default admin credential.

## 6. Mail (Postfix + Dovecot) and DNS (PowerDNS) provisioning patterns —
HestiaCP / ISPConfig prior art and PowerDNS's own recommendation

*(Synthesized from direct reads of the HestiaCP and ISPConfig 3 source trees,
Dovecot's docs, Postfix's docs, and PowerDNS's own authoritative-server
documentation.)*

**Correction to initial assumption**: HestiaCP's MTA is **Exim4, not
Postfix** — its installer actively removes/conflicts-out Postfix
(`install/hst-install-ubuntu.sh`), uses Exim's `lsearch{}` lookups against
flat per-domain `passwd`/`aliases` files
(`install/deb/exim/exim4.conf.template`, layout created by
`bin/v-add-mail-domain`), and Dovecot is wired only as a SASL/IMAP auth
backend via the `passwd-file` driver against those same flat files — not SQL.
HestiaCP also has **no PowerDNS support at all** — it's pure BIND9 with flat
zone-file templates. So HestiaCP is not usable as a Postfix/PowerDNS
reference; **ISPConfig 3 is the actual SQL-backed Postfix + PowerDNS
precedent**, and is what the recommendations below are drawn from (with
Dovecot's and PowerDNS's own docs as the normative source for the parts
ISPConfig gets wrong — see DNS below).

- **Postfix virtual mailbox backend — SQL-backed, not flat-file maps.**
  ISPConfig wires `virtual_mailbox_maps` to `proxy:mysql:{config_dir}/mysql-
  virtual_mailboxes.cf`, a live query against a `mail_user`/`mail_domain`
  schema (`install/tpl/mysql-virtual_*.cf.master`,
  `install/tpl/debian_postfix.conf.master`) — the `proxy:` prefix routes
  through Postfix's `proxymap(8)` to pool/cache the DB connection. Dovecot's
  SQL passdb/userdb (`install/tpl/debian_dovecot-sql.conf.master`) query the
  same table. This needs no `postmap` regeneration step at all for routine
  CRUD — a new mailbox is a single `INSERT`, usable on the very next
  delivery/login, with **no Postfix reload required** (only `postfix reload`
  for structural `main.cf`/`master.cf` changes). This matches CyberPanel's own
  approach (§5) and is the standard "Perfect Server" pattern. **Boron
  decision: SQL-backed virtual mailboxes** via `proxy:mysql:` lookup maps,
  schema modeled on ISPConfig's `mail_domain`/`mail_user` two-table pattern,
  stored in Boron's own database so the provisioning daemon owns one
  source of truth (no separate mail-only DB to keep in sync).
- **Dovecot integration**: passdb/userdb query the same SQL tables; delivery
  is via LMTP (`virtual_transport = lmtp:unix:private/dovecot-lmtp` in
  current ISPConfig versions), not local(8) or piped `--exec-mail deliver`.
  Mailbox format: **Maildir**, required for LMTP delivery. Storage
  convention: `/var/vmail/<domain>/<user>/` under one shared system `vmail`
  user/group (ISPConfig uses uid/gid 5000) — not real Linux login accounts —
  so adding/removing a mailbox only ever touches SQL, never `useradd`.
- **Password hashing**: ISPConfig reimplements `crypt()` in PHP (SHA512-CRYPT
  preferred). HestiaCP instead shells out to `doveadm pw`, preferring
  **BLF-CRYPT (bcrypt) → ARGON2ID**, only falling back to MD5-CRYPT for
  legacy compatibility. Dovecot's own docs list ARGON2ID/ARGON2I (requires
  libsodium, Dovecot ≥2.3) and BLF-CRYPT (bundled, no extra dependency) as its
  strongest schemes, and now disable MD5-CRYPT/DES-CRYPT by default.
  **Boron decision: generate hashes via `doveadm pw -s ARGON2ID` (falling
  back to `BLF-CRYPT` if Argon2 support is unavailable)** rather than
  reimplementing crypt() in application code — guarantees Dovecot-compatible
  output by construction and avoids a "rolled my own password hashing" bug
  class, following HestiaCP's approach rather than ISPConfig's.
- **PowerDNS backend and API — REST API over the built-in webserver (port
  8081), not direct SQL writes.** ISPConfig's PowerDNS plugin
  (`server/plugins-available/powerdns_plugin.inc.php`) writes PowerDNS's own
  `domains`/`records` tables directly via raw SQL — and as a direct
  *consequence* has to manually reimplement what the API gives for free:
  shelling out to `pdns_control rediscover`/`notify`/`retrieve` after every
  write, and `pdnsutil rectify-zone` after every record change to keep DNSSEC
  ordering correct. **PowerDNS's own documentation explicitly advises against
  this**: *"It is highly recommended to not use raw SQL commands to
  manipulate records in the database. Either use the REST-API or pdnsutil."*
  (doc.powerdns.com/authoritative/backends/generic-sql.html). The REST API
  (`localhost:8081`, `/api/v1`, `X-API-Key` header) is backend-agnostic,
  handles cache invalidation/notification internally, and reduces DNSSEC to a
  single declarative `"dnssec": true` field on the zone object (internally
  equivalent to `pdnsutil zone secure` + `zone rectify`) instead of an
  imperative multi-step workflow. **Boron decision: drive PowerDNS
  exclusively through its REST API**, never raw SQL against PowerDNS's
  schema — this also gets correct-by-construction zone/rrset validation
  (PowerDNS itself rejects malformed records) instead of hand-validating
  record syntax in Boron before an insert. Backend storage underneath the
  API (`gsqlite3` vs `gmysql` vs `lmdb`) is a separate, lower-stakes choice
  the PowerDNS docs don't prescribe — see ARCHITECTURE.md for Boron's
  pick.
- **DNSSEC**: off by default per-zone in PowerDNS; enabling it is a single API
  field but still an explicit per-zone opt-in. Boron v1 leaves DNSSEC
  disabled (not in the v1 record-type scope) — this is a pure PowerDNS-side
  setting an operator can flip later with no Boron schema change.
- **DKIM/SPF/DMARC**: HestiaCP auto-generates a DKIM keypair for every new
  mail domain by default and auto-publishes DKIM/SPF/DMARC TXT records *only
  when it also hosts that domain's DNS zone*. ISPConfig — a mature,
  widely-deployed panel — generates DKIM only on admin request and has **no
  SPF automation at all**. This confirms skipping SPF/DKIM/DMARC automation
  is an acceptable v1 posture: Boron's generic A/AAAA/CNAME/MX/TXT zone
  editor is sufficient for an operator to add these records by hand; no
  special-cased automation is built in v1 (matches the locked v1 scope, which
  lists only those five record types).
- **Reload safety**: neither incumbent panel actually pairs `postfix
  check`/`doveconf -n` with every reload (ISPConfig has no such call
  anywhere; HestiaCP validates web/proxy configs but not mail) — but
  Postfix's and Dovecot's own docs confirm both checks are cheap, fast, and
  exit non-zero on bad config, and the project's own requirement ("every
  phase touching system config must validate before reload + rollback") is
  stricter than either incumbent's actual practice. **Boron adds this
  validation deliberately, beyond what HestiaCP/ISPConfig themselves do**:
  `postfix check` + `postconf -n` before `postfix reload`, `doveconf -n`
  before `systemctl reload dovecot`, mirroring the OLS `-t`-before-restart
  pattern (§2) — so all three system-config surfaces (OLS, Postfix, Dovecot)
  share one validate→reload→verify→rollback code path in the provisioning
  daemon. Both incumbents *do* validate one good practice worth keeping:
  reload (HUP, graceful, in-flight connections finish under the old config)
  is preferred over restart, reserved for structural changes only — which
  pairs naturally with SQL-backed mail needing no reload at all for routine
  mailbox CRUD.

## 7. Open questions carried into ARCHITECTURE.md

- Exact directory layout and privilege-separation boundary (web API process vs.
  root provisioning daemon) — addressed in ARCHITECTURE.md.
- Backend/frontend framework choice for the API + admin UI — addressed in
  ARCHITECTURE.md (not a research question, a build decision).
- Real Let's Encrypt issuance has no real domain to test against; ARCHITECTURE.md
  records the `sslip.io` wildcard-DNS-to-IP workaround used for the v1 E2E test
  (server's public IP is 104.234.179.64, so `104-234-179-64.sslip.io` resolves
  back to this box with zero registration needed).
