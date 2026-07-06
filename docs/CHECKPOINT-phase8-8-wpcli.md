# CHECKPOINT phase8-8 — WP-CLI UI

**Goal:** auto-detect WordPress (wp-config.php); core update, plugin/theme
list/update/activate/deactivate, user reset-password, cache flush, search-replace
(preview first), maintenance mode; async, runs as the account user.
API: `POST /accounts/{u}/wordpress/{id}/wpcli`.

## What was built

- **Shared async runner** `daemon/cmdjobs.py` (`CommandRun` model): runs a
  validated argv **as the account user** (`runuser -u <user> -- env HOME=...
  <argv>`) on a bounded executor, capturing stdout/stderr/exit-code into a
  CommandRun row (same async-job shape as WordPressJob). A one-time
  `revealed_secret` (a reset password) is returned on first poll then cleared.
- **Daemon** `daemon/wpcli.py` (ops `wpcli.detect/run/get/list`):
  - `detect_installs`: scans the account's own domain docroots for
    `wp-config.php`; reads the WP version straight from
    `wp-includes/version.php` (no wp-cli run). `id` = the domain.
  - `run_wpcli`: an **allowlisted** action → argv builder (never a shell string);
    user values (slugs, search/replace, login) are discrete argv elements.
    `user_reset_password` generates a strong password, passes it via
    `--user_pass=` **redacted from the daemon log**, and reveals it once.
    `search-replace` runs `--dry-run` when `preview` (preview-first).
  - `ensure_wpcli`: downloads `wp-cli.phar` server-wide if missing, verified via
    `--version`.
- **API** `api/routers/devtools.py` — `POST /accounts/{u}/wordpress/{id}/wpcli`,
  `GET .../wordpress/detect`, `GET .../wordpress/wpcli/runs[/{job_id}]`.
- **Frontend**: Dev Tools page → WP-CLI tab (install picker, action selector with
  conditional inputs, run + live-polling output, one-time password reveal).

## LIVE verification (this server)

`wp-cli.phar` (WP-CLI **2.12.0**) was downloaded server-wide to
`/usr/local/bin/wp-cli.phar` and run as a disposable account user: `wp core
download` succeeded and `wp core version` read the real WP install (7.0),
proving the WP-CLI execution path works as the account user. (A full `plugin
list` needs a configured DB, i.e. a provisioned WP site; the argv is unit-tested
and the same runner is proven live via Composer's `vendor/` creation below.)

## Tests

`tests/test_wpcli.py` — 9 tests: allowlist/validation (bad action, bad slug),
plugin-update all-vs-named, search-replace preview/apply, password
reveal+redaction, detection (finds/ignores), and run argv construction +
wp-config guard. All green.
