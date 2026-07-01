# Checkpoint: Phase 3, Feature 3 — phpMyAdmin auto-login per database

## What was built

- **phpMyAdmin deployed once, server-wide** (`apt install phpmyadmin`,
  `dbconfig-install` skipped — same non-interactive-setup pattern as
  Roundcube, Phase 2 feature 3), gated behind a new `pma_hostname`
  setting exactly like Roundcube's `webmail_hostname` — an operator who
  never sets it sees no reference to phpMyAdmin anywhere in the
  generated OLS config.
- **Token-based signon**, using phpMyAdmin's own documented `auth_type =
  signon` mechanism (not a scrape of its login form): `daemon/pma.py`'s
  `create_token()` creates a **fresh, ephemeral MariaDB user** scoped to
  exactly one database (same `HOSTED_DB_PRIVILEGES` grant every hosted
  database already uses), generates a short-lived (15 min default),
  single-use, server-generated token, and writes the ephemeral
  credentials to a small per-token JSON file. `templates/
  pma_signon.php.j2` (deployed into phpMyAdmin's own docroot) reads the
  token, resolves the file, **deletes it before trusting its contents**
  (single-use enforcement), and hands phpMyAdmin's signon auth a session
  it recognizes.
  - The real db_user's own password is **never** read/reused/exposed —
    Forgehost never stores it (see `daemon/mariadb.py`), so a fresh
    throwaway MariaDB user is the only way to grant scoped access at
    all, and it also means a phpMyAdmin session can't accidentally leak
    the account's actual, ongoing-use database password.
  - A periodic cleanup script (`scripts/pma_token_cleanup.py`, cron,
    mirroring `usage_snapshot.py`/`backup_scheduler.py`'s established
    pattern) drops the ephemeral MariaDB user + any still-present token
    file once a `PmaToken` row passes its expiry, whether or not it was
    ever redeemed — otherwise an unused token leaks a throwaway MariaDB
    account forever.
- **UI**: a "Manage" button next to each database on the account detail
  page, `POST .../databases/{db}/pma-token` → redirects straight to the
  pre-authenticated phpMyAdmin URL.
- **API**: `POST /accounts/{u}/databases/{db}/pma-token` (per the goal).

## Three real bugs found by live testing (none caught by unit tests, all three environment/system-integration issues, not application logic)

1. **`/usr/share/phpmyadmin`'s stock ownership (root:root) failed OLS's
   minimum-uid/gid check**, identical to the exact failure class
   Roundcube hit in Phase 2 feature 3 (`CGIRLimit.minUID`/`minGID`) —
   `openlitespeed -t` rejected the vhost with "Uid of
   /usr/share/phpmyadmin/ is 0, smaller than minimum requirement 11."
   **Fixed** the same way Roundcube was: `chown -R www-data:www-data
   /usr/share/phpmyadmin` (documented as a manual one-time setup step in
   README, matching Roundcube's precedent — Forgehost's own daemon code
   does not chown a system package's files automatically, only what it
   itself creates).
2. **A missing PHP dependency surfaced as a plain HTTP 500 with an empty
   Forgehost-managed error log.** phpMyAdmin's `MoTranslator` component
   does `require_once 'Symfony/Component/ExpressionLanguage/
   autoload.php'` via a *relative* path, which depends on
   `/usr/share/php` being in PHP's `include_path`. Ubuntu's own system
   php.ini patches that in by default; **LiteSpeed's bundled `lsphp83`
   ships its own, separate php.ini with a bare `.` include_path**, so
   the same file that resolves fine under Ubuntu's stock PHP+Apache
   stack 404'd under lsphp. Diagnosed by temporarily forcing
   `display_errors` on for one request (the per-vhost errorlog directive
   never captured this — PHP fatals go through PHP's own `error_log` ini
   setting, not OLS's vhost log, a distinction worth remembering for any
   future PHP-level debugging in this project). **Fixed** with a
   `phpIniOverride { php_admin_value include_path ".:/usr/share/php" }`
   in `templates/pma_vhost.conf.j2`, scoped to phpMyAdmin's own vhost
   only (no other vhost's `include_path` changes).
3. **The token directory's *group* ownership, not just its mode bits,
   was wrong.** `_token_dir()` originally did `mkdir(mode=0o770)` and
   separately `chown`'d each token *file* to `root:www-data`, but never
   chowned the *directory* itself — it defaulted to group `root` (the
   creating process's own primitive group), which `www-data` isn't a
   member of. Every signon attempt 403'd with "token not found" even
   though the token file was sitting there with correct permissions,
   because `www-data` couldn't even traverse into the containing
   directory to read it. **Fixed** by explicitly `chown`ing the
   directory itself to `root:www-data` (idempotent, every call) — the
   same "mode bits alone aren't enough without matching group
   ownership" lesson `server.py`'s `amain()` already encodes for
   `/run/forgehost`'s socket directory, now applied here too.

All three were found and fixed via the exact same "run the goal's
DONE WHEN scenario live" discipline this project has followed since
Phase 1 — none would have been caught by the mocked unit test suite,
which correctly tests the *logic* (token scoping, expiry, single-use)
but cannot exercise real OLS/PHP/filesystem-permission integration.

## Testing

`tests/test_pma.py` (new, 8 tests): token creation requires an existing,
owned database; happy-path token creation (ephemeral user distinct from
the real db_user, token file content correct); no-`pma_hostname`
degrades to `pma_url: null` rather than erroring; two tokens for two
different databases get two independent ephemeral users; expired-token
cleanup drops both the MariaDB user and the file (whether or not
redeemed); unexpired tokens are left alone; `bootstrap_pma_files()`
generates a real config + signon script and reuses (doesn't rotate) an
existing `blowfish_secret` on repeat calls. 357 tests passing (up from
349).

## Live verification performed

1. Installed phpMyAdmin, fixed all three bugs above via live iteration.
2. Created a real account with two databases (`shop`, `blog`), minted a
   token scoped to `shop`.
3. Redeemed the token via the real signon URL over HTTPS — confirmed a
   real `200`/`302` phpMyAdmin session, no password prompt.
4. **Confirmed database scoping at the actual MySQL grant level** (the
   authoritative test, not UI inspection): `SHOW GRANTS FOR
   'pma_<random>'@'localhost'` showed privileges on `shop` only; a
   direct `mysql -u pma_<random> ... USE blog` was rejected with
   `Access denied for user ... to database 'blog'`, while `USE shop`
   succeeded — this is the goal's DONE WHEN bar ("cannot access other
   DBs with the same token") confirmed at its actual enforcement point.
5. Confirmed single-use: redeeming the same token twice — first
   succeeds (`302`), second returns `403 token not found, already used,
   or expired`.
6. Confirmed malformed tokens are rejected (`400`) before any file
   lookup is even attempted.
7. Confirmed the UI's "Manage" button (real HTTP request through
   FastAPI, session-cookie authenticated) redirects straight to a
   working, pre-authenticated phpMyAdmin session.
8. Terminated the test account, confirmed (as documented/expected) the
   ephemeral `pma_*` MariaDB users are untouched by account termination
   (they're independent of the account's own DB grants) — then ran
   `scripts/pma_token_cleanup.py` after forcing their expiry and
   confirmed all of them were dropped along with their `PmaToken` rows.

## What's untested / explicitly out of scope

- A genuine cloud/remote MariaDB host (this project's MariaDB is always
  local via Unix socket — signon's `host_port` is hardcoded to
  `localhost`, matching every other DB-access path in this project).
- Real concurrent redemption race (two simultaneous requests for the
  exact same token) — the file-delete-then-use ordering makes this
  correct in principle (whichever request's `unlink()` wins reads valid
  data, the other sees `ENOENT`), not stress-tested under real
  concurrency.
- phpMyAdmin's own further internal authorization (e.g. its "Users"/
  "Privileges" admin pages) was not explored beyond confirming the
  signon session itself is correctly scoped -- the ephemeral MariaDB
  grant is the actual security boundary regardless of what phpMyAdmin's
  UI does or doesn't expose.
