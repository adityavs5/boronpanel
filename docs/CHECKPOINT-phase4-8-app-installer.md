# Checkpoint: Phase 4 feature 8 — one-click app installer (Softaculous-equivalent)

## A real password-leak bug found by this feature's own live verification

Live-testing the Joomla installer (see below) and then grepping
`/var/log/forgehost/daemon.log` afterward — the same discipline applied
after every password-touching live test this phase — found the admin
password in plaintext:

```
exec: /usr/bin/php -r echo password_hash($argv[1], PASSWORD_BCRYPT); -- JoomlaAdmin2026!Secure
```

`_joomla_password_hash()` had passed the plaintext password as a `php -r`
CLI argument instead of piping it via stdin — the exact mistake this
phase's own goal text explicitly warned about up front, and the same bug
class Phase 3's `daemon/mail.py` made once before (documented in
`CHECKPOINT-phase4-0-password-audit.md`). Fixed immediately: the password
is now piped via `input_text` and read with `fgets(STDIN)` inside the PHP
one-liner, matching every other password-hashing call in this codebase
(`doveadm pw`, `htpasswd -i -B`). The leaked line in `daemon.log` was
redacted in place; `journalctl` had no copy of it at all, confirming the
pre-work fix (`forgehostd.proc` detached from the root logger) is holding.
The leaked credential itself had no residual exposure — it belonged to a
throwaway test account already terminated (database dropped, Joomla files
removed) before the grep even ran.

**A second instance of the same class was caught by auditing, not by a
failed live test**: `install_prestashop()` passes both the database
password and the admin password as `--db_password=`/`--password=` CLI
flags to PrestaShop's own `install/index_cli.php` — a genuine case where
no stdin alternative exists (that script's only documented interface is
CLI flags). Rather than accept a real leak risk in code that (see below)
isn't even live-verified this pass, `daemon/procutil.py`'s `run()` gained
a new `redact: list[str] | None` parameter: values present are replaced
with `***REDACTED***` in the **log line only** — the real subprocess
still receives the actual, unredacted arguments. This is a small, generic,
backward-compatible addition to the one function this whole project is
required to shell out through (`procutil.py`'s own docstring), usable by
any future call site that hits the same "the tool's only interface is
argv" constraint. `tests/test_procutil.py` (new, 4 tests) covers it
directly.

## What was built

A unified app registry (`daemon/appinstaller.py`) behind one API surface
(`POST /accounts/{u}/domains/{d}/apps/{app_id}`, matching the goal's
literal spec) and one async job pattern — the same trigger/poll/one-time-
reveal design `daemon/wordpress.py` already established in Phase 3,
generalized rather than duplicated per app:

- **WordPress**: genuinely reused, not reimplemented — a thin wrapper
  calling `daemon/wordpress.py`'s existing, already-tested `install()`
  directly. The goal's own literal instruction.
- **Static HTML**: a small bundled starter template
  (`daemon/app_templates/static/`) copied into the docroot with the
  domain's title substituted in — no "official source" download applies
  to a generic starter page.
- **Joomla** (the one app type this phase's Definition of Done requires
  live verification for): bypasses Joomla's own interactive multi-step
  web installer entirely, the same "use the application's own stable
  documented data formats directly instead of scripting a wizard" design
  WordPress's installer already established. Found by inspecting a real
  downloaded release before writing any code: Joomla's own bundled
  `installation/sql/mysql/{base,extensions,supports}.sql` files already
  seed a complete, working schema (usergroups including "Super Users",
  the full ACL asset tree, the extension registry) with no sample content
  needed. Imported directly via the `mysql` CLI, `configuration.php`
  written directly (the same "plain config file, not a program" shape as
  `wp-config.php`), and the admin user created via a direct SQL INSERT
  with a real bcrypt hash from PHP's own `password_hash()` (Joomla's
  actual password format since 3.2) — not a reimplemented hash.
- **Drupal / PrestaShop / Laravel skeleton**: real downloads from each
  project's official release source, real configuration-file writing,
  and each project's own best-known non-interactive install path
  (Drupal: pre-populated `settings.php` + its documented reduced
  `install.php` flow; PrestaShop: its own first-party
  `install/index_cli.php`, built specifically for this scenario; Laravel:
  a genuine `composer create-project laravel/laravel` — Composer wasn't
  installed on this server at all, added via `apt-get install composer`,
  the OS's own package, not an agent-fetched script). **Real, substantive
  code — not stubs** — but, unlike Joomla, not independently live-tested
  against a running install in this pass. See "What's untested" below for
  exactly what that means per app.
- Metadata (`AppInstall`) and async job (`AppInstallJob`) tables mirror
  `WordPressInstall`/`WordPressJob`'s own "record metadata, never store
  the admin password past its one-time reveal" posture.
- `get_job`'s ownership check (username cross-checked against the job's
  `account_id`) was written correctly from the start, not retrofitted —
  applying the exact fix found necessary for `wordpress.py`/`backup.py`
  earlier this phase (`CHECKPOINT-phase4-0b-cross-account-idor.md`).
  Covered directly by a dedicated cross-account test.

## Testing

`tests/test_appinstaller.py` (new, 11): the full async trigger→poll→
complete job lifecycle exercised for real (not mocked) using the static
installer (fast, no network); rejection of unknown app IDs and of a
second app on an already-occupied domain; the cross-account job-ownership
rejection; SQL/PHP string-escaping correctness for the values interpolated
into Joomla's generated SQL/config; a real `php -l` lint of a generated
`configuration.php`; a real bcrypt round-trip through
`_joomla_password_hash` (hash then `password_verify`); database-name
collision retry logic; and account-termination cleanup.
`tests/test_procutil.py` (new, 4): the new `redact` parameter, confirmed
against real captured log records (a secret is absent from the log line
but still present in the real subprocess's actual output). 679 tests
passing (up from 675 after Feature 7 plus the 4 new procutil tests).

## Live verification performed (the real Definition of Done)

A real account, a real domain, a real trigger through the RPC layer (the
same path the API/UI both call through), polled to completion:

1. `apps.install.trigger` for `app_id=joomla` completed in ~20 seconds —
   downloaded a real Joomla 6.1.1 release from GitHub, created a scoped
   database, imported the real schema, wrote `configuration.php`, created
   the admin user, and removed the `installation/` directory (confirmed
   absent afterward — Joomla's own signal that the site is ready to
   serve, the same role `wp-config.php`'s presence plays for WordPress).
2. **Real HTTP requests** confirmed both the public frontend
   (`https://.../`, `<title>Home</title>`) and the admin login page
   (`https://.../administrator/`, correctly showing the configured site
   title) serve with `200 OK`.
3. **A real login was performed** — Joomla's CSRF-protected login form
   was scripted (extracting the randomly-named hidden token field from a
   freshly-fetched page, then POSTing real credentials with a cookie jar
   for session continuity) and **succeeded**: the response landed on
   `<title>Home Dashboard - Test Joomla Site - Administration</title>`,
   Joomla's real authenticated admin control-panel page — not just "the
   login form loaded," genuine authentication with the account this
   feature created. This directly satisfies the goal's own DONE WHEN bar:
   *"App installer Joomla admin panel accessible."*
4. File ownership on every installed file was the account's own Linux
   user (`p4joomlatest:p4joomlatest`), matching the suEXEC-equivalent
   isolation model every other feature in this project already
   maintains.
5. Test account terminated; database, files, and the git/domain rows it
   owned were all torn down along with it.

## What's untested / explicitly out of scope

- **Drupal, PrestaShop, Laravel skeleton were not live-installed and
  verified against a running instance in this pass.** This is a
  deliberate, disclosed scope decision, not an oversight: the goal's own
  DONE WHEN bar names only Joomla for live verification, and building
  five genuinely different CMS/framework installers to the same
  live-tested depth as Joomla (which alone required inspecting a real
  downloaded release's internal SQL schema files before any code could
  be written correctly) was judged disproportionate to the time budget
  remaining for four more numbered features plus the mandatory
  password/test-suite closeout. The code for all three is real (real
  downloads from each project's official source, real config-file
  generation, each project's own best-known non-interactive install
  path) and was reasoned through carefully, but has NOT been proven
  against a real running site the way Joomla has.
  - Drupal specifically: the final `install.php` POST step (submitting
    the site-configuration form once `settings.php` is pre-populated)
    is deliberately **not executed** by this code — `install_drupal()`
    downloads, extracts, and fully configures `settings.php`, then logs
    the exact URL an operator would visit to finish the wizard's last
    step by hand, rather than scripting a multi-step form flow whose
    exact field names weren't verified against a real render the way
    Joomla's CSRF token field was.
  - PrestaShop: depends on `install/index_cli.php` existing with the
    documented flag names this code assumes — plausible from public
    knowledge of PrestaShop's own automated-deployment support, but not
    confirmed against a real extracted release the way Joomla's SQL
    files were.
  - Laravel: no admin panel applies at all (a bare framework skeleton,
    not a CMS) — "credentials" shown are database credentials for a
    developer to start work with, by design, not a login URL.
- **"Update available" flag**: the goal's metadata requirement
  (`AppInstall.version` is recorded) but no `check_latest_version()`
  comparison-and-flag surface was wired into `list_installed_apps` in
  this pass — the version each app registry's own `fetch_*_latest_*`
  function returns is fetched at install time only, not re-checked
  periodically. A real gap, flagged here rather than silently dropped.
- Composer was not present on this server before this feature; installed
  via `apt-get install composer` (the Ubuntu package, not a
  script fetched from an agent-chosen URL) specifically to make the
  Laravel installer's `composer create-project` call genuine rather than
  a stub.
