# Phase 7a Feature 6: PHP version per domain

## What was built

Extends the existing per-account `Account.php_version` (Phase 2 feature 1)
with an optional per-domain override:

- `Domain.php_version` (new nullable column, migrated live onto the existing
  `domains` table) — `NULL` means "inherit `Account.php_version`" (the
  default for every pre-existing and newly-created domain, so nothing
  changes for any domain that never touches this feature).
- `handlers_domain.set_domain_php_version` — validates the requested version
  against `settings.php_versions` (the same allowed set the account-level
  selector already uses), empty/`None` clears the override, then calls
  `ols.refresh_vhost(account)` (this project's existing declarative
  full-regen pattern — see "hot-reload" note below).
- **`daemon/ols.py`'s core rendering change**: `_all_active_vhosts()` used
  to declare exactly **one** PHP `extProcessor` per account
  (`account.php_version`). It now declares one extProcessor per **distinct
  effective PHP version actually in use** across an account's own domains
  (`d.php_version or account.php_version`, deduplicated per account) — most
  accounts still get exactly one (every domain inheriting the account
  default, unchanged from before), but an account with one overridden
  domain now gets two side-by-side `lsphp` backends, each domain's own
  vhost `scripthandler` pointing at whichever one matches its own effective
  version (`render_vhost_conf`'s `php_app_name` computation updated to
  match). This is the piece that makes two different PHP versions actually
  *work concurrently* under the same account — declaring the override
  without this would have left the overridden domain's vhost referencing a
  nonexistent extProcessor, failing `openlitespeed -t`.
- **API**: `PATCH /accounts/{u}/domains/{d}/php-version` (self-service, same
  posture as the existing account-level PHP version endpoint — a customer
  manages their own domains' PHP version, not an admin-only action).
- **UI**: a per-domain dropdown (`inherit (<account default>)` or any
  installed version) on the account detail page's domains table.
- **"Hot-reload that vhost only, no impact to other domains/accounts"**:
  interpreted functionally, matching this project's own established,
  locked-in architecture (`ARCHITECTURE.md` SS6/SS7: httpd_config.conf is
  *always* declaratively regenerated in full and reloaded through the
  shared validate→backup→apply→reload→verify→rollback pipeline — there is
  no incremental per-vhost config patching anywhere in this codebase, by
  design, for every other feature already built). A PHP-version change
  therefore does trigger a full config regen+graceful-reload exactly like
  every other account-level change in this project already does — but its
  actual *functional* effect is scoped to the touched domain's own
  scripthandler target (and, if no sibling domain shares its previous
  effective version, removing that now-unused extProcessor); every other
  domain (this account's and every other account's) keeps serving
  throughout the graceful reload, the same guarantee every other
  account-level config change here already provides.

## Real bugs found

None specific to this feature — it's a targeted, backward-compatible
extension of an existing, already-hardened rendering path
(`_all_active_vhosts`/`render_vhost_conf`), and the live verification below
passed on the first real attempt.

## Live verification (real, on this VM)

Two domains under the same disposable account (`p7anodetest`, terminated
after verification):

- `p7acachetest.104-234-179-64.sslip.io` — left on the account default.
- `p7aphptest.104-234-179-64.sslip.io` — explicitly set to PHP 8.1 via the
  real `domain.set_php_version` RPC. The call succeeded (config validated
  and reloaded without error) — direct confirmation the two-extProcessor
  rendering is syntactically correct on this real OLS build.
- A one-line `<?php echo phpversion();` page on each domain, requested via
  real `curl` over HTTPS:
  - `p7acachetest...` → `8.3.31` (the account's own default, PHP 8.3).
  - `p7aphptest...` → `8.1.34` — **a genuinely different PHP version,
    served concurrently, under the same Linux account, at the same time.**
- Clearing the override (`php_version=None`) reverted `p7aphptest` back to
  `8.3.31` on the very next request, and `p7acachetest` was re-confirmed
  still `8.3.31` throughout the entire sequence — direct proof the override
  toggle doesn't disturb the account's other domain.

This fully satisfies the goal's own literal Definition of Done for this
feature: "two domains on same account with different PHP versions confirmed
via phpinfo()" (a bare `phpversion()` call was used in place of a full
`phpinfo()` page — the same, simpler check Phase 2 feature 1's own original
PHP-version-switch verification already used for the account-level
selector, for the identical reason: it's the one line of that page's output
that actually matters for this check).

## What's untested / deferred

- An account with **three or more** distinct effective PHP versions across
  its domains was not exercised (only two were tested) — the
  `versions_seen` dedup logic is a plain `set`, with no reason to expect it
  to behave differently at three versions than at two, but not
  independently re-confirmed live.
- Switching a domain's override while real, sustained traffic is hitting a
  *different* domain under the same account (a live "no impact" stress
  test, as opposed to the sequential curl checks actually performed) was
  not attempted.
