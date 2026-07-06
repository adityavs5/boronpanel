# Checkpoint: Phase 7b Feature 1 — cPanel backup import

## What was built

`daemon/cpanel_import.py` imports a standard cPanel/WHM full-account backup
tarball into a brand-new Forgehost account: Linux user + home dir, domains
(with DNS zones re-published via PowerDNS), MariaDB databases, Postfix/
Dovecot mailboxes, SSL certs (validated before trust), cron jobs, and
best-effort FTP sub-accounts. Async job (`CpanelImportJob`), per-item
success/fail/skip report appended incrementally as each item is processed,
matching the goal's explicit "fail gracefully on unsupported items, import
rest" requirement — only two steps are fatal to the whole job (archive
extraction, target-account creation); every domain/database/mailbox/DNS
record/cert/cron-job/FTP-account is independently try/excepted.

- **API**: `POST /api/v1/admin/import/cpanel` (multipart: `username` +
  either a `file` upload or a `url`), `GET .../{job_id}`, `GET ` (list).
- **UI**: `/ui/admin/import/cpanel` (upload form + job list),
  `/ui/admin/import/cpanel/{job_id}` (per-item report, auto-refreshing
  while running).
- Uploaded files are spooled to a plain `/tmp` file by `forgehost-api`
  (unprivileged) and read by `forgehostd` (root, which can read any file
  regardless of who wrote it) — no new shared-directory permission scheme
  needed; the daemon deletes the spooled file itself the moment it's done
  reading it, success or failure (it can hold real customer data — DB
  dumps, mailbox contents).

## Provenance note (read before assuming this was built from scratch)

This module's initial ~900-line implementation was written by a second,
independent Claude Code session that was, unbeknownst to this one,
concurrently running the exact same Phase 7b goal against this same git
working tree (both sessions had been resumed via `claude --continue` on
what turned out to be the same underlying autonomous `/goal` loop — see
"What's honestly still open" below for the full account). Rather than
discard genuinely solid, convention-consistent work and rebuild it, this
session adopted it, reviewed it in full, found and fixed two real bugs in
it (below), then built everything around it (RPC wiring, API router, UI,
tests, this checkpoint). Flagging this plainly rather than silently
presenting adopted code as originally written in this pass.

## Real bugs found by review, fixed here

1. **Docroot permission widening via `shutil.copytree`.** `_copy_homedir`
   uses `shutil.copytree(src, dst, dirs_exist_ok=True)` to lay imported
   site files into an already-created (mode 0750) docroot. Confirmed live
   with a throwaway `copytree` call: when the target directory already
   exists, `copytree`'s own `copystat()` call resets its mode to match the
   *source* directory — a cPanel `public_html` commonly ships mode 0755,
   so every single import silently widened the new account's docroot back
   to world-readable, reopening the exact vulnerability
   `ARCHITECTURE.md §6` documents fixing (any local Linux account can
   `cat` another account's files). Fixed by adding a
   `docroot-perms:<domain>` step, run after the file copy for every
   imported domain, that re-calls `handlers_domain.ensure_docroot` — the
   same "re-assert the permission model after restoring files from an
   external source" lesson `daemon/backup.py`'s own restore path already
   had to learn (tar doesn't preserve ACLs; this is the same failure
   *class* via a different mechanism, `copystat` resetting mode instead of
   simply not preserving an ACL). Confirmed by a small standalone
   reproduction (a real `chmod 0750` + `copytree` + `chmod` check) before
   fixing.
2. **dnspython silently truncates MX/CNAME targets to a relative label.**
   `_parse_bind_zone_records` calls `dns.zone.from_file(..., origin=domain,
   check_origin=False)` — by default this *relativizes* every name to the
   zone's own origin, including a `Name` **value** referenced from an
   rdata field (MX's `exchange`, CNAME's `target`), not just the record's
   own label. A zone file's `mail.example.com.` MX exchange came back from
   `str(rdata.exchange)` as just `mail` — a real parse the first
   attempted test caught, not a hypothetical: writing `"10 mail"` as a
   PowerDNS MX record's content is simply wrong record data. Fixed with
   `.derelativize(origin)` on the `MX.exchange`/`CNAME.target` fields
   specifically (record *labels* are correctly left relative — that's what
   `powerdns.upsert_record`'s own `_record_name` expects). Regression-
   tested with a real BIND zone file containing both an MX and a CNAME
   record, asserting the fully-qualified form survives.

**Two more found by a second, adversarial review pass** (conducted in place
of live testing once live deployment was confirmed blocked this pass — see
"What's honestly still open" below — rather than leaving the code
unreviewed):

3. **`_rewrite_wp_config` accepted a partial substitution.** The original
   check only required *at least one* of DB_NAME/DB_USER/DB_PASSWORD/
   DB_HOST to be found and replaced — a migrated site whose wp-config.php
   happened to define these in a style the regex didn't match on some of
   them (rather than all) would silently end up with a mix of old and new
   credentials (e.g. the new DB_USER/DB_PASSWORD but the OLD, now-
   nonexistent DB_NAME), which fails to connect with no obvious cause.
   Fixed to require all four to be found, raising a clear
   `CpanelImportError` naming exactly which constant(s) are missing
   otherwise — reported as a per-item failure (`wordpress:<domain>`), not
   an import-aborting one, matching the goal's own "fail gracefully...
   import rest" rule.
4. **`_import_mysql_dump` didn't strip the dump's own database context.**
   A real, well-known cPanel/WHM migration gotcha: `mysqldump --databases`
   (a common way full-account backup tooling produces per-database dump
   files) prepends its own `CREATE DATABASE ...;`/`USE ...;` lines naming
   the *original* database. Fed to `mysql <target_db>` as-is, the dump's
   own `USE` statement silently overrides the caller-specified target for
   every statement after it — this would likely surface as an access-
   denied failure (`forgehost_daemon`'s grants are scoped per-database),
   but in the worst case, if a same-named database happened to already
   exist, could write into the wrong one entirely. Fixed by stripping any
   `CREATE DATABASE`/`USE`/`DROP DATABASE` line from the dump text before
   import, so it always lands in the caller-specified database regardless
   of what the original dump's own header says.

## Tests

35 new tests (`tests/test_cpanel_import.py`): content-root detection (both
`cpmove-<user>/`-wrapped and unwrapped layouts), YAML/line-fallback
metadata parsing, domain dedup/kind assignment, DB dump→suffix naming, a
**real** dnspython BIND-zone parse (A/AAAA/MX/CNAME/TXT, confirming the
fix above), cron-line parsing, FTP passwd-line parsing, wp-config.php
in-place rewrite (including the `$`-in-password PHP-interpolation
regression class this project has hit before), SSL cert validation via
real `cryptography`-generated certs (expired / domain-not-covered /
mismatched-key all correctly rejected; wildcard SAN match accepted; a
valid cert is actually installed to the real `letsencrypt_cert_paths`
location and cleaned up after), the docroot-permission-reassertion fix
itself (real filesystem + real `setfacl`), tar-slip extraction safety, and
job lifecycle/ownership guards (duplicate account rejected, concurrent
import rejected, `get_job` cross-username access rejected), plus (added
during the second review pass) the all-four-constants-required wp-config
validation and the mysqldump `CREATE DATABASE`/`USE`-stripping fix.

## Methodology note: how bugs 3–4 were found without live testing

With live deployment confirmed blocked this pass (see below), this
module got a **second, deliberately adversarial read-through** — assume
every helper is wrong until proven otherwise by a concrete test or
counter-example — in place of the live end-to-end run this project would
normally rely on to catch exactly this class of bug. Both bugs 3 and 4
were confirmed with a small, targeted reproduction before being labeled
real (bug 4's underlying MySQL behavior is well-documented, not
independently re-derived from first principles) rather than asserted from
code-reading alone. This is a real, disclosed substitution for this
project's usual "live testing catches what mocks can't" discipline, not
equivalent to it — a live end-to-end import could still surface issues
neither the mocked suite nor this review pass would.

## What's honestly still open

- **No real cPanel/WHM instance exists in this sandbox to generate a
  genuine backup from.** The parser follows WHM's own publicly documented
  "Backup File and Directory Structure" layout (`homedir/`, `mysql/*.sql`,
  `userdata/*.yaml`, `dnszones/*.db`, `ssl/{certs,keys}/`, `cron/<user>`,
  `homedir/etc/<domain>/passwd`), and every parsing/import step has its own
  unit test against a hand-built, format-accurate fixture — but an actual
  end-to-end run against a real cPanel export, and the goal's own "site
  serves after" live check, were **not** performed. Same category as this
  project's own prior external-system substitutions (no owned domain for
  real SSL validation, no real attacker to unban for fail2ban) — disclosed
  rather than silently assumed equivalent.
- **Live deployment/verification was blocked this pass.** `scripts/
  deploy.sh` (which restarts `forgehostd`/`forgehost-api`) was denied by
  this environment's own permission classifier, specifically because
  another Claude Code session was confirmed concurrently active against
  this same live server with no clear authorization for *this* deploy at
  *this* moment — a real, correct safety call, not a bug to work around.
  Every feature this phase is therefore verified by its mocked test suite
  only; the live checks the goal's own "Done when" section asks for
  (import a real backup and confirm the site serves, numbers matching
  independent log totals, an email actually received end-to-end, a
  webhook actually delivering to a real endpoint, a live 80% disk alert)
  are **not yet independently confirmed against the real running
  services** and should be the first thing done on wake-up, once only one
  session is active against this server.
- **FTP sub-account import is genuinely best-effort.** A per-account
  `cpmove` backup only sometimes includes `homedir/etc/<domain>/passwd`
  (cPanel's own FTP account list) — when present it's parsed and each
  account gets a freshly generated password (the stored hash uses
  cPanel's own scheme, not necessarily PureDB's, so it can't be reused);
  when absent, no FTP items appear in the per-item report at all rather
  than being reported as a hard failure, since a backup lacking that file
  isn't itself abnormal.
- **Addon-domain docroot relocation** (`_relocate_addon_docroot`) trusts
  `userdata/<domain>.yaml`'s own recorded `documentroot` field to compute
  the source-relative path to move into place — not independently
  verified against a real WHM export's actual field name/shape (some
  cPanel versions may use a different key), so a docroot that doesn't
  match Forgehost's own `<home>/<domain>` convention could still end up
  needing manual correction after a real import.
- Mailbox/FTP passwords are, correctly and deliberately, never recoverable
  from a cPanel backup — every imported mailbox/FTP account gets a fresh
  random password, reported in the per-item detail string so the operator
  knows to redistribute credentials; this matches `daemon/backup.py`'s own
  established restore-time posture for the identical reason.

## Live verification result (2026-07-06): BLOCKED (environment, not a feature defect)
Ran `verify_phase7b_live.py cpanel_import`. The async import job timed out
at 120s: account provisioning stalls on this host's OpenLiteSpeed
graceful-reload crash (OLS 1.9.0 dies on SIGUSR1 with status=255,
reproducible even with a valid zero-vhost config — see STATUS.md "OLS
reload defect"). Not an import-code defect. Re-run once OLS reload is
fixed on this box.

## UPDATE (2026-07-06, 2nd live run): import PASSES; a real bug was fixed
Supersedes the BLOCKED note above. The OLS reload defect was root-caused and
fixed (host `/tmp` restored to 1777 — see STATUS.md), so the import now runs
to completion: job reported all items OK against a real synthetic WHM backup
— Linux account + home-dir copy, primary domain + DNS, docroot perms,
**MariaDB database imported**, cron, WordPress correctly skipped. **Bug found
and fixed**: `cpanel_import_staging_dir` never existed on disk and
`tempfile.mkdtemp(dir=...)` raised *outside* the job's error handler,
stranding the job at "fetching backup archive"/running with no error. Fixed
in daemon/cpanel_import.py with `os.makedirs(..., mode=0o700, exist_ok=True)`
inside a handler that fails the job cleanly; dir created (0700 root:root).
tests/test_cpanel_import.py 35/35 pass. The only unmet part — the final
`curl` returning HTTP 500 — is the Phase 6b namespace-container defect that
breaks per-account PHP host-wide (see STATUS.md), not an import defect.

## FINAL (2026-07-06): cPanel import PASSES live end-to-end
Supersedes both notes above. After clearing stale OLS namespaces
(`lsnsctl unmount-all` + restart — see STATUS.md "Phase 6b namespace
staleness"), a fresh import completed with all items OK and the imported site
served **HTTP 200**. Done-When #1 met. The staging-dir bug fix
(`os.makedirs(..., exist_ok=True)` inside the job's error handler) is retained;
`tests/test_cpanel_import.py` 35/35.
