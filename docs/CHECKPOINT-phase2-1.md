# Checkpoint: Phase 2, Feature 1 — PHP version selector

## Scope decision: 7.4/8.0 substituted with 8.4/8.5

The goal asked for PHP 7.4/8.0/8.1/8.2/8.3. **7.4 and 8.0 are not available**
— confirmed via `apt-cache search`/`apt-cache policy` against LiteSpeed's
official repo for Ubuntu 24.04 (noble): no `lsphp74` or `lsphp80` package
exists at all for this distro release. Both PHP versions are EOL upstream;
LiteSpeed doesn't maintain `noble` builds for EOL versions. The only way to
get them would be installing packages built for an older Ubuntu/Debian
codename, which risks glibc/OpenSSL ABI mismatches against this system's
actual libraries and means running PHP builds with no upstream security
fixes available regardless of source — not a conservative choice for a
hosting panel handling arbitrary customer code.

**Decision**: support **8.1, 8.2, 8.3, 8.4, 8.5** instead — all natively
available, free, installed and verified working
(`shared/config.py: Settings.php_versions`).

## What was built

- Installed `lsphp82`, `lsphp84`, `lsphp85` (+ common extensions) alongside
  the existing `lsphp81`/`lsphp83` from Phase 1.
- `daemon/handlers_account.py`: `set_php_version()`, wired through the same
  hook-list pattern as suspend/unsuspend/terminate (`PHP_VERSION_HOOKS`,
  populated in `server.py` to call `ols.refresh_vhost()` — reused as-is
  from Phase 1's SSL deploy-hook plumbing, no new OLS-side function needed).
- `PATCH /api/v1/accounts/{username}/php-version` — **self-service**, not
  admin-only (`require_account_access`, matching cPanel-equivalent
  precedent: PHP version is a customer's own setting, unlike suspend/
  terminate).
- UI dropdown on the account detail page.
- 5 new unit tests (happy path, unknown-version rejection, no-op when
  unchanged, rejected on a terminated account, unknown account).

## A real bug found by live testing: PHP version switch silently didn't
take effect

First live test: switched `phptest1` from 8.3 → 8.1 via the REST API,
daemon logs showed a full successful validate→reload→verify cycle, the
regenerated `vhconf.conf` correctly showed the new `lsphp81` path — but a
live `phpversion()` request kept returning `8.3.31`.

**Root cause**: `templates/vhost.conf.j2`'s LSAPI `address` (the Unix
socket path OLS uses to reach the backend) was keyed only by
`{{ username }}`, not by PHP version. Every version an account ever used
shared the exact same socket path
(`uds://tmp/lshttpd/<username>.sock`). OLS's own LSAPI backend-process
bookkeeping appears to key off that socket address rather than the
`extprocessor` block's name — so after a version switch, OLS kept treating
the address as "already provisioned" and didn't spawn a fresh backend
process for the new binary, even though the graceful reload correctly
applied everything else.

**Fix**: socket path now includes the PHP version
(`uds://tmp/lshttpd/{{ php_app_name }}.sock`, where `php_app_name` is
already `<username>_php<ver>` — the same string used for the
`extprocessor`/`scripthandler` names). This gives every (account, PHP
version) pair a genuinely distinct backend identity, so a version switch
can never be confused with routing to the previous version's process.

## Real end-to-end verification performed

Two accounts (`phptest1`, `phptest2`), both created at the 8.3 default.
Confirmed both serving `8.3.31` via real HTTP requests. Switched
`phptest1` through the REST API to 8.1 (initially silently failed to take
effect — the bug above), then after the fix: to 8.2, 8.4, and 8.5 in
sequence, each time confirming via a real HTTP request that
`phpversion()` reported the new version *and* `posix_geteuid()` still
reported the correct account owner (isolation intact) — while `phptest2`,
untouched throughout, kept reporting `8.3.31` the entire time, confirming
the switch is genuinely scoped to one account. Both accounts then
`terminate`d cleanly. Full 166-test suite (161 existing + 5 new) passing
throughout.

## What's untested

- No test exercises switching a *suspended* account's PHP version (allowed
  by the code — `status in ("active", "suspended")` — but not exercised
  live).
- Concurrent PHP-version switches on different accounts (each is an
  independent `ConfigWriterMulti` transaction against the shared
  `httpd_config.conf`; same untested-concurrency caveat noted in Phase b's
  checkpoint applies here too, not newly introduced by this feature).
