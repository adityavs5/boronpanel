# CHECKPOINT phase8-9 — Composer UI

**Goal:** install / update / require {package} / dump-autoload; runs in the app
dir as the account user, async; install composer server-wide if missing.
API: `POST /accounts/{u}/composer`.

## What was built

- **Daemon** `daemon/composerui.py` (ops `composer.run/get/list`): builds a
  fixed-command argv (install/update/dump-autoload/require), jails the app
  directory via the file-manager realpath check (must be within the account
  home), and submits to the shared `daemon/cmdjobs.py` runner (runs
  `composer` as the account user, async). `ensure_composer` uses the installed
  `/usr/bin/composer` (Composer 2.7.1) and would download `composer.phar`
  server-wide if it were missing. The `require` package spec is validated
  (`vendor/name[:constraint]`) and passed as a discrete argv element.
- **API** `api/routers/devtools.py` — `POST /accounts/{u}/composer`,
  `GET .../composer/runs[/{job_id}]`.
- **Frontend**: Dev Tools page → Composer tab (command select, app-dir,
  package for require, live-polling output).

## LIVE verification (this server) — Done-When met

As a disposable account user (uid 1003), a `composer.json` requiring `psr/log`
was `composer install`ed via the exact runner:
- exit 0, **`vendor/psr/log` created**, and `vendor/` is **owned by the account
  user (uid 1003), not root** — "install runs, vendor/ created" ✅.

## Tests

`tests/test_composer.py` — 12 tests: package validation (ok + injection
rejection), run_composer install/require/unknown, and the shared `cmdjobs`
runner (records success/failure, one-time secret reveal, foreign-account
rejection). All green.
