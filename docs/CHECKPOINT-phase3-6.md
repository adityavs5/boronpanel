# Checkpoint: Phase 3, Feature 6 — Per-account PHP ini editor

## What was built

- **New `PhpIniOverride` table** (one row per account, optional) rather
  than new columns on `Account` -- deliberately, to sidestep Phase 2
  feature 6's own documented schema-migration gotcha
  (`Base.metadata.create_all()` only creates new tables, never `ALTER`s
  existing ones). A new table needs no manual migration step on an
  existing install; this is the second feature in this project to make
  that choice explicitly for that reason (the first was Phase 3 feature
  1's `DkimKey` table).
- **Rendered via OLS's own native per-context `phpIniOverride` block**
  (`templates/vhost.conf.j2`), which already existed for `open_basedir`
  -- extended rather than inventing a separate mechanism. This directly
  satisfies "stored... in account's LSAPI context dir, not system-wide":
  the override lives in that account's own `vhconf.conf` (under
  `/usr/local/lsws/conf/vhosts/<vhost>/`), regenerated only when that
  account's own vhost is regenerated (`ols.refresh_vhost`), never
  touching the shared `httpd_config.conf`'s content for any other
  account. `php_admin_value` (not `php_value`) is used throughout --
  admin-enforced, not overridable by a customer's own `ini_set()` or
  `.htaccess`, appropriate for a shared-hosting security posture.
- **Conservative validation**, per the goal's explicit callout:
  `memory_limit`/`upload_max_filesize`/`post_max_size` reject `-1`/`0`
  ("unlimited") outright and are capped at 2048M; `max_execution_time`
  rejects `0` ("unlimited") and is capped at 300s; `error_reporting` is
  restricted to the character set a legal PHP bitmask expression can
  actually use (`E_*` constants, `&|~^()`, digits, whitespace) --
  this value is interpolated directly into an OLS config file
  (`php_admin_value error_reporting "<value>"`), so this is the actual
  injection defense for that field, not a formality (mirrors the same
  "validate before it reaches a config file" rule this project already
  applies to DNS record values, cron commands, etc.). Also enforces
  PHP's own real constraint that `post_max_size >= upload_max_filesize`
  (PHP silently truncates/rejects uploads otherwise -- a genuinely
  helpful validation, not just defensive).
- **API**: `GET`/`PATCH`/`DELETE` at `/accounts/{u}/php-ini` (goal asked
  for `PATCH`; `GET`/`DELETE` added for symmetry with every other
  resource in this project and because the UI needs to read current
  values to prefill the form). **UI**: a card on the account detail
  page (not a separate page -- the goal calls for "a simple form per
  account", and this is one of six fields, small enough to fit
  alongside the existing PHP-version card it sits next to) with a
  "reset to server defaults" action.

## Testing

`tests/test_handlers_php_ini.py` (new, 13 tests): defaults-when-unset,
full happy path, **partial updates preserve other fields** (a `PATCH`-
shaped update, not a full replace), every validation rejection
(unlimited memory/execution-time, oversized memory, malformed size,
post_max_size-below-upload_max, and an explicit injection-attempt string
for `error_reporting`), reset, idempotent reset, and account-termination
cleanup -- `daemon.ols.refresh_vhost` mocked (this project's established
"don't touch real OLS/filesystem state in the unit suite" convention).
`tests/test_validation.py` extended (10 new tests) for the new
validators directly. `tests/test_ols.py` extended (3 new tests)
confirming the rendered vhost config both omits the block entirely when
unset and renders every field correctly (including the boolean ->
`"On"`/`"Off"` mapping) when set. 423 tests passing (up from 389).

## Live verification performed

1. Created a real account + domain, set a full custom override
   (`memory_limit=384M`, `upload_max_filesize=96M`,
   `post_max_size=96M`, `max_execution_time=45`, `display_errors=On`,
   `error_reporting=E_ALL`) via the RPC layer.
2. Dropped a real `phpinfo()` page into that account's docroot and
   fetched it over real HTTP -- confirmed every one of the six values
   shown in the real, rendered `phpinfo()` output exactly matches what
   was set (the goal's own DONE WHEN bar: "verify via phpinfo()").
3. Created a **second**, untouched account with its own `phpinfo()` page
   -- confirmed it still shows the plain system default (`memory_limit:
   128M`), proving the override applies to exactly one account and
   nothing else, not just that the daemon call succeeded.
4. Exercised the account-detail page's PHP-ini form through real HTTP
   (session-cookie authenticated): submitted a change, reloaded the
   page, confirmed the new value is both applied and correctly shown as
   pre-filled/"custom overrides active" on reload.
5. `account.terminate` confirmed to clean up the override row (no
   orphaned settings row survives termination, consistent with this
   project's general "no orphaned resources" requirement).

## What's untested / explicitly out of scope

- `.user.ini`-style per-directory overrides within a single account
  (e.g. a customer wanting different settings for a subdirectory) --
  out of the goal's stated scope, which is account-level only.
- A live cgroup-memory-vs-PHP-memory_limit interaction stress test (Phase
  2 feature 6's cgroup `MemoryMax` and this feature's `memory_limit` are
  two independent caps at two different layers -- both apply, whichever
  is hit first wins; not jointly stress-tested here).
