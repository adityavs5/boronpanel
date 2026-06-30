# Checkpoint: Phase b — OLS vhost + PHP/LSAPI templating

## What was built

- `templates/vhost.conf.j2` — per-account OLS vhost config: one LSAPI
  external app (`extUser`/`extGroup` pinned to the account's own uid/gid),
  a `context /` that's either the real docroot or (when suspended) the
  static suspended page with a catch-all rewrite rule, an ACME HTTP-01
  webroot context for Phase f, and per-vhost access/error logs.
- `templates/httpd_config.conf.j2` — the full top-level OLS config,
  regenerated from scratch on every change from the DB's accounts/domains
  (declarative reconciliation, not incremental text patching of a file
  shared across every account — ARCHITECTURE.md §6/§7).
- `daemon/configtx.py` gained `ConfigWriterMulti`, generalizing the Phase a
  validate→backup→apply→reload→verify→rollback engine across the two files
  (`vhconf.conf` + `httpd_config.conf`) that must land together.
- `daemon/ols.py` — rendering, the OLS-specific validate/reload/verify
  callables (including the `openlitespeed -t`-only-validates-live-tree
  adaptation, documented in ARCHITECTURE.md §7), and
  `provision_vhost`/`suspend_vhost`/`unsuspend_vhost`/`terminate_vhost`/
  `bootstrap_baseline`.
- `daemon/handlers_domain.py` — `domain.add`/`domain.list`, including
  docroot/logs/tmp/acme-challenge directory setup and the POSIX ACL grant
  described below.
- Wired into `daemon/server.py`: `domain.add`, `domain.list`,
  `system.bootstrap_ols`, and `ols.suspend_vhost`/`unsuspend_vhost`/
  `terminate_vhost` registered as `handlers_account` hooks.
- 17 new unit tests (`tests/test_ols.py`, `tests/test_handlers_domain.py`),
  76 total passing.

## Real end-to-end verification performed

All against this live VM, through the running daemon, with PHP 8.1 and 8.3
both installed:

1. Ran `system.bootstrap_ols` once to replace OLS's stock "Example" vhost
   (see "what was fixed" below) with a clean Forgehost-managed baseline.
2. `account.create` + `domain.add` for a fresh account (`freshtest`) → real
   `openlitespeed -t` passed, `systemctl reload lshttpd` succeeded, a real
   HTTP request to the new vhost (`Host: freshtest.local`) returned the
   actual PHP output, which itself reported `posix_geteuid()` as
   `freshtest` — proof PHP genuinely executes as the account's own Linux
   user, not `nobody` or `root`.
3. Same test repeated over HTTPS (port 443, the bootstrap default
   self-signed cert) — HTTP 200, same per-account identity.
4. **Cross-account isolation**: a second unrelated account (`other1`) was
   given a real shell (`su -s /bin/bash other1`) and tried to `cat` the
   first account's `index.php` — `Permission denied`, both before and after
   the permission-model fix below (the fix changed *why* it's denied, not
   whether).
5. `account.suspend` → requests to *any* path on the vhost (not just `/`)
   return the static suspended page (HTTP 200, suspended HTML) via the
   catch-all rewrite rule; `account.unsuspend` → normal PHP serving resumes
   immediately, confirmed by another live request.
6. `account.terminate` → Linux user gone, home dir gone, vhost config dir
   gone, zero references to the account left in `httpd_config.conf`,
   `openlitespeed -t` still exits 0 afterward.
7. Re-ran the full pytest suite (76 tests) after every fix below.

## Bugs found by real testing and fixed (not theoretical — each reproduced
live on this VM before the fix)

1. **Orphaned DB row on failed OLS apply.** `domain.add` must commit the
   `Domain` row before calling `provision_vhost` (the renderer reads all
   domains for the account from the DB), so a failed OLS apply left a
   `Domain` row in SQLite with no corresponding vhost ever applied. Fixed
   with an explicit compensating delete (plus clearing
   `Account.primary_domain`) in `add_domain`'s except path. Covered by
   `test_add_domain_compensates_db_row_when_ols_apply_fails`.
2. **Phantom vhost from a stale `Account.primary_domain`.** A fallback in
   `_all_active_vhosts` synthesized a vhost entry from `Account.primary_domain`
   when no `Domain` rows existed — which is exactly the field bug #1 could
   leave inconsistent. Removed the fallback entirely; `Domain` rows are now
   the only source of truth for what gets a vhost/listener entry.
3. **`openlitespeed -t` exits non-zero on the stock, untouched install** due
   to the bundled "Example" vhost's docroot uid/gid being below OLS's own
   configured minimum. This would have made *every* rollback look like a
   failure, since rollback restores whatever was live before — including,
   on a fresh install, the stock config. Fixed with a one-time
   `system.bootstrap_ols` op that replaces the stock config with a clean
   baseline before any account is provisioned (see README for when to run
   this on a fresh install).
4. **Missing ACME webroot directory.** The vhost template always declares a
   context for `/.well-known/acme-challenge/`; OLS's `-t` rejects a context
   whose `location` doesn't exist. Fixed by creating that directory at
   domain-add time rather than deferring to Phase f.
5. **Permission model was wrong twice in a row, both caught by real
   cross-process testing, not by reasoning about it:**
   - First attempt (home dir + docroot mode 750): real HTTP requests 403'd,
     because OLS's "DocRoot UID" setting does not make the main worker
     process switch uid for static-file serving — confirmed by testing, not
     assumed.
   - Second attempt (docroot mode 755, "make it world-readable"): requests
     worked, but a second unrelated Linux account could `cat` the first
     account's PHP source — confirmed by an actual cross-account read
     attempt, not just inferred from the permission bits.
   - **Final fix**: home dir `711` (traverse-only), docroot `750` (no
     "other" access) + a POSIX ACL granting the `nobody` user specifically
     `rX`, applied recursively with a default ACL so future uploads inherit
     it. Passed both the functional and the isolation test. ARCHITECTURE.md
     §6 was corrected in place to document the wrong turns, not just the
     final answer, since the wrong turns are exactly what the next person
     touching this code would otherwise redo.
6. **A stray duplicated line** (`os.chmod(tmp_dir, 0o750)` left outside its
   function during an edit) caused a `NameError` on the very next real test
   run — caught immediately because every change in this phase was verified
   against the live daemon, not just unit tests.

## What's untested

- Subdomain/addon-domain vhost stanzas beyond a single primary domain per
  account (the data model and template support multiple domains per vhost
  via the `map` line, but no addon domain was exercised end-to-end yet).
- OLS's native cgroups v2 resource limiting (ARCHITECTURE.md §6 mentions it
  as a goal) was **not implemented** in the vhost template — only the
  longstanding `memSoftLimit`/`memHardLimit`/`procSoftLimit`/`procHardLimit`
  External App fields (confirmed present in the stock config, so known-safe
  to use) were used. cgroups v2 config block syntax wasn't verified against
  a real install in the time available; flagged here rather than guessed at.
- Concurrent `domain.add` calls racing the same regenerated
  `httpd_config.conf` — the daemon processes RPCs from a thread pool
  (`asyncio`'s default executor), and nothing currently serializes OLS
  config transactions against each other. A race could mean one of two
  concurrent `provision_vhost` calls' renders briefly clobbers the other's
  before the next one regenerates from DB state again (DB state itself is
  consistent; the *applied* OLS config could transiently miss one of two
  domains added in the same instant). Low risk for v1's expected usage
  pattern (one admin, sequential actions) but worth a lock around OLS
  config transactions if concurrent provisioning becomes common.

## What to review first on wake-up

- The permission-model fix (#5 above) is the highest-stakes change in this
  phase from a security standpoint — worth an independent read of
  `daemon/handlers_domain.py`'s `_ensure_docroot`/`_grant_webserver_acl` and
  the corrected ARCHITECTURE.md §6 before trusting it further.
- `system.bootstrap_ols` is destructive-ish in spirit (replaces the stock
  OLS config) even though it's safe on a fresh install — make sure the
  README's setup instructions call it out as a required one-time step, not
  optional.
