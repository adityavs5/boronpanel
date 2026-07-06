# Phase 7a Feature 4: LSCache integration

## What was built

Per-domain LSCache via OLS's native `cache` module (`daemon/lscache.py`,
`shared/models.py`'s `LscacheSettings` — one row per domain, created lazily
on first enable):

- **Confirmed empirically, not assumed** (before writing any rendering
  code): this server's own installed OLS docs (`Module_Help.html`: "module
  parameters... can override this setting at the Listener, Virtual Host, or
  Context levels") and its own module definition
  (`/usr/local/lsws/modules/cache.def`, listing the real per-instance
  parameter names this build's cache module accepts —
  `storagepath`/`noCacheUrl`/`expireInSeconds`/`purgeUri`/etc.) — unlike
  ModSecurity (`ARCHITECTURE.md` SS10.5, which genuinely has no per-vhost
  config at all on this OLS build), LSCache **is** overridable per virtual
  host. `httpd_config.conf.j2`'s existing server-level `module cache {}`
  block (`enableCache 0`) stays the global default; enabling LSCache for a
  domain renders a second, vhost-scoped `module cache {}` override inside
  that domain's own `vhconf.conf` (`daemon/ols.py`'s new
  `_lscache_for_domain`/`_app_proxy_map`-style wiring, following the exact
  same "absence means unchanged default behavior" convention
  `_php_ini_for_account` already established).
- **Enable/disable, TTL, exclude paths** — `enabled`, `ttl_seconds`
  (30s–7d bounds), `exclude_paths` (rendered as `noCacheUrl` lines, reusing
  `validate_redirect_path`'s exact charset/shape since both are bare URL
  paths interpolated into an OLS config the same way).
- **Purge**: a real filesystem operation, not a live HTTP round-trip through
  `purgeUri`'s exact semantics (which is *also* rendered, as a documented,
  secondary/manual mechanism, but not what `purge()` itself relies on) —
  each cache-enabled vhost gets its own explicit `storagepath`
  (`/usr/local/lsws/cachedata/<vhost_name>/`), so purging a domain's cache
  means clearing exactly that directory's contents. Deterministic,
  scriptable, no dependency on guessing live HTTP purge-trigger semantics.
- **WordPress auto-detect + plugin reminder**: checks for `wp-config.php`
  (WordPress installed) and `wp-content/plugins/litespeed-cache/
  litespeed-cache.php` (the official plugin installed) directly on disk —
  no WP-CLI, no network call. A reminder message is surfaced when WordPress
  is detected but the plugin isn't.
- **Stats**: real, honest, verifiable numbers — a live count of cached
  object files on disk and the last purge timestamp. Deliberately **not** a
  fabricated hit/miss counter: this OLS build has no simple aggregate
  hit/miss source exposed anywhere short of parsing every access-log line
  with a custom log format this project doesn't otherwise use, and claiming
  numbers that aren't real would violate this project's "verify, don't
  assert" discipline.
- **API/UI**: `/api/v1/accounts/{u}/domains/{d}/lscache` (GET, PUT, POST
  `/purge`, GET `/stats`), a UI tab linked from each domain's row on the
  account page.
- **Cleanup**: `handlers_domain.remove_domain` and a new `TERMINATE_HOOKS`
  entry both call `lscache.delete_settings_for_domain`/
  `terminate_account_lscache` — same manual-cascade convention as
  Redirect/FileAuthDir.

## A real bug found live during this feature's own verification: `purge()`
silently broke caching forever after the first use

**The bug**: the first implementation of `purge()` did
`shutil.rmtree(storagepath)` followed by `storagepath.mkdir(...)`. OLS's own
worker process (`nobody`, per `httpd_config.conf.j2`'s top-level
`user`/`group`) creates this directory *itself* the first time it writes a
cache entry, with a specific mode (`drwxrws---`, setgid, group-writable —
confirmed by direct inspection) it needs in order to keep writing new
entries later. Recreating the directory from `forgehostd`'s own root
identity replaces it with `root:root 0755` — `nobody` can then never write a
new cache entry into it again, silently breaking caching for that vhost's
entire remaining lifetime (confirmed live: three requests to a dynamic PHP
page each showed a *different* embedded timestamp after a purge, versus the
*same* frozen timestamp before it — proof caching had stopped engaging at
all, not just an empty cache warming back up).

**The fix**: `purge()` now only ever removes the directory's *contents*
(files and subdirectories), never the directory itself — if the directory
doesn't exist at all yet (LSCache enabled but OLS never actually cached
anything for this vhost), there is nothing to purge, and it is deliberately
**not** created here either, for the identical ownership reason. Re-verified
live afterward: directory's own inode/mode/ownership survive a real
`lscache.purge` call unchanged, and caching resumes correctly on the very
next request pair. A regression test
(`test_purge_preserves_storage_directory_ownership_and_mode`) asserts the
directory's inode and mode are unchanged across a purge.

## Live verification (real, on this VM)

Disposable domain `p7acachetest.104-234-179-64.sslip.io` (account
`p7anodetest`, terminated after verification) with a real PHP page that
embeds `date('Y-m-d H:i:s')` in its output and sends `Cache-Control: public,
max-age=3600` itself:

- **Before** enabling LSCache: `curl -I` shows no cache-related header;
  every request re-executes the PHP script (fresh timestamp each time).
- **After** `lscache.set` (enabled, TTL 3600, one excluded path): config
  validated (`openlitespeed -t` passed, confirming the guessed
  `storagepath`/`noCacheUrl`/`purgeUri` raw-config syntax is actually
  correct on this build) and reloaded without error.
- **Genuine cache-hit proof**: two requests to the same dynamic PHP URL, 2+
  real seconds apart, returned the **identical embedded timestamp** — the
  script did not re-execute the second time. A real cached object file was
  confirmed on disk under the vhost's own `storagepath`.
- `curl -I` shows the real `cache-control: public, max-age=3600` response
  header — the goal's own literal DONE WHEN check.
- `lscache.purge` (after the ownership fix) cleared the real cached file,
  `lscache.stats` correctly reported `cached_object_count: 0`, and the
  **very next** request pair re-cached correctly (same-timestamp behavior
  resumed) — confirming purge doesn't leave the feature in a broken state.
- WordPress detection tested via unit tests only this pass (no real
  WordPress install was re-used from an earlier phase for this specific
  domain) — the detection logic itself (`wp-config.php` / plugin file
  presence) is a plain, already-covered filesystem check.

## What's untested / deferred

- A real WordPress install with the actual LiteSpeed Cache plugin installed
  was not exercised end-to-end this pass (an earlier phase's WordPress
  installer work is unrelated to this specific feature's own domain) — the
  plugin-detection logic itself is unit-tested and the underlying OLS cache
  mechanism is independently confirmed live via a plain dynamic PHP page.
- `noCacheUrl` (exclude paths) was configured but not independently
  live-verified to actually bypass caching for that specific path (only the
  overall enable/disable/TTL/purge behavior was exercised against the
  domain's main page).
- Query-string cache-key variation (`qsCache`, inherited unchanged from the
  server-level default) was not exercised.
