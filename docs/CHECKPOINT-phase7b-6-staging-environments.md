# Checkpoint: Phase 7b Feature 6 — Staging environments

## What was built

One-click clone of a domain to `staging.<domain>`: new OLS vhost, files
copied, database cloned with a staging-prefixed name (WordPress sites
only — detected by `wp-config.php`'s presence, not requiring a
`WordPressInstall` row, so a manually-uploaded or cPanel-imported WP site
is equally staging-cloneable), `wp-config.php` rewritten in place to point
at the staging database and URL, staging domain added to DNS (free, via
the existing subdomain path), SSL via Let's Encrypt, plus a sync button
to re-clone from production.

- `daemon/staging.py` deliberately reuses existing provisioning primitives
  end to end rather than building a parallel path:
  `handlers_domain.add_domain` (Phase 2 feature 4) provisions
  `staging.<domain>` exactly like any other subdomain — docroot creation
  (mode 0750 + the "nobody" ACL grant), the Forgehost-managed-zone DNS A
  record, and the OLS vhost render all come for free, no staging-specific
  vhost-rendering code was written at all. Database dump/restore reuses
  `daemon/backup.py`'s own `_dump_database`/`_restore_database_dump`
  helpers (the same ones `daemon/cpanel_import.py`, feature 1, already
  reuses for the identical "dump this DB, import it into that one"
  reason — one implementation, not three).
- `_rewrite_staging_wp_config`: in-place field substitution (DB_NAME/
  DB_USER/DB_PASSWORD/DB_HOST only) preserving salts/table-prefix/any
  custom constants verbatim, using `wordpress._php_str` for the
  replacement literal — the same PHP-double-quote-interpolation
  protection `daemon/cpanel_import.py`'s own wp-config rewriter needs, for
  the identical reason (a generated MariaDB password containing a literal
  `$` would otherwise corrupt the file, Phase 6b Step 1's documented bug).
  `WP_HOME`/`WP_SITEURL` are then prepended (PHP's `define()` is
  first-caller-wins, so this is guaranteed to take effect even against a
  production wp-config that itself defines these further down) — the
  standard, well-known technique for pointing a cloned WordPress install
  at a different URL without touching `wp_options` directly (which would
  require assuming a specific table prefix/schema shape).
- **Compensation on failure**: a failed step after the staging domain was
  provisioned removes that domain (same "commit a resource, compensate on
  a later step's failure" pattern `handlers_domain.add_domain` itself
  already uses); a failed step after the staging database was allocated
  drops it too.
- **Sync** re-copies files fresh (wiping whatever was in staging) and, for
  a WordPress site, re-dumps/imports into the *same* staging database
  (`mysqldump`'s own default `DROP TABLE IF EXISTS` per table means
  importing over existing tables correctly replaces content, not merely
  appends) — since the staging DB's original password was never persisted
  anywhere (this project's "passwords are never stored" rule, same
  posture `daemon/backup.py`'s restore path already takes for mailbox
  passwords it can't recover), sync generates a **fresh** password and
  writes it back into the freshly-recopied wp-config.php rather than
  needing to have remembered the old one.
- **Termination**: the staging domain/database are already torn down
  generically by the existing `ols.terminate_vhost`/`handlers_database.
  terminate_account_databases` TERMINATE_HOOKS (a staging environment is,
  deliberately, nothing more than an ordinary `Domain` + `DatabaseGrant`
  row scoped to the account) — this feature only needed one new hook to
  clean up its own `StagingEnvironment` bookkeeping row.
- **API**: `POST`/`GET`/`DELETE /api/v1/accounts/{u}/domains/{d}/staging`,
  `POST .../staging/sync` (matches the goal's literal spec).
- **UI**: a "Staging" link per non-staging domain on the account detail
  page, a dedicated page showing the staging URL, WordPress/database
  status, and create/sync/delete buttons.

## Real bug found and fixed during review (same class as feature 1's)

`_copy_files` uses `cp -a` (not `shutil.copytree`, deliberately — see
below) to lay production files into the already-provisioned staging
docroot, then `chown -R`s the result. Reviewing this against the exact
`shutil.copytree`-resets-mode bug found and fixed in
`daemon/cpanel_import.py` (`CHECKPOINT-phase7b-1`) surfaced the same risk
here by a different path: a recursive copy of any kind can leave the
target directory's effective permissions matching the source rather than
Forgehost's own required 0750 + ACL. Fixed by having `_copy_files`
unconditionally re-call `handlers_domain.ensure_docroot` after every
copy (both `create_staging` and `sync_staging` go through this one
function) — confirmed with a real filesystem test (`chmod 0755` source →
copy → target's mode reasserted to `0750`), not just reasoned about.
`cp -a` itself was chosen over `shutil.copytree` specifically *because*
of this project's own established "prefer a real system tool over a
Python reimplementation for file operations" precedent
(`daemon/backup.py`/`daemon/wordpress.py` use `tar`/`cp` via
`daemon.procutil.run`, not `shutil`) — it does not, on its own, avoid the
permission-reassertion need, which is why the explicit re-`ensure_docroot`
step is still required regardless of which copy mechanism is used.

## A second real bug, found by a later adversarial re-review

With live deployment confirmed blocked this pass, this module (along with
every other feature) got a second, deliberately adversarial read-through
in place of live testing (see `CHECKPOINT-phase7b-1`'s methodology note).
That pass found `_rewrite_staging_wp_config` had the exact same class of
gap as `daemon/cpanel_import.py`'s own wp-config rewriter (found and
fixed in the same pass, `CHECKPOINT-phase7b-1`): it substituted whichever
of DB_NAME/DB_USER/DB_PASSWORD/DB_HOST it found a `define()` for and
silently left the rest untouched if the regex didn't match — a staging
clone with, say, the OLD production DB_NAME still in place alongside the
NEW staging DB_USER/DB_PASSWORD would fail to connect at all (the staging
db user has no grants on the production database name), with no clear
error pointing at why. Fixed to require all four constants to be found,
raising a clear `StagingError` naming exactly which one(s) are missing —
matching the goal's own "validate config before apply, rollback on
failure" rule, and letting the existing compensation logic (drop the
staging database, remove the staging domain) handle the failure cleanly
rather than leaving a half-migrated staging site in place.

## Tests

27 new tests (`tests/test_staging.py`): pure helpers (`_is_wordpress`,
`_source_db_name`, `_staging_domain_for`), `_rewrite_staging_wp_config`
(DB-const substitution, `WP_HOME`/`WP_SITEURL` injection ordering, the
`$`-in-password protection, missing-file guard, the all-four-constants-
required regression test), `_copy_files` tested at
the real-filesystem level (a genuine `chmod`+copy+permission-reassertion
check, the regression test for the bug above), and the full
`create_staging`/`sync_staging`/`get_staging`/`delete_staging`
orchestration with file-copy and DB-clone mechanics mocked out (already
covered at the lower level) to isolate the orchestration logic itself:
WordPress vs. non-WordPress paths, duplicate-creation rejection, a
staging-domain-name collision with a real existing `Domain` row rejected,
compensation (orphan-free) on a failure at each of two different stages,
sync re-copying and re-cloning with a rotated password, and account-
termination bookkeeping cleanup (including the no-staging-existed no-op
case).

## What's honestly still open

- **"Clone a WordPress domain to staging, wp-admin accessible"** (the
  goal's own Done-When criterion) needs a live WordPress install to clone
  and a real HTTP request to the resulting `wp-admin/` — not performed
  this pass, blocked by the same live-deployment restriction documented in
  `CHECKPOINT-phase7b-1`. Every individual mechanism this feature
  composes (subdomain provisioning, DB dump/restore, wp-config rewriting)
  is itself either freshly unit-tested here or was independently
  live-verified in an earlier phase (Phase 2 feature 4 subdomains, Phase 3
  feature 2 WordPress, Phase 2 feature 7 backup/restore) — what's
  unconfirmed is this feature's own new orchestration wiring those
  together, live.
- SSL issuance for the staging domain is deliberately best-effort
  (logged, not raised, on failure) — the same "this environment has no
  real public NS delegation for any test domain" limitation every prior
  SSL-issuing feature in this project's history has already documented
  (`CHECKPOINT-phase7a-5-wildcard-ssl.md`, the original Phase f DNS-01
  finding) applies identically here and was not re-investigated.
- A staging environment created for a **non-primary** (addon) domain that
  is later removed via the ordinary `handlers_domain.remove_domain` path
  leaves its `StagingEnvironment` bookkeeping row and staging
  domain/database orphaned (only full account *termination* cleans up
  staging state generically) — a real, narrow edge case, not fixed this
  pass since the goal doesn't ask for it and doing so would mean
  `handlers_domain.remove_domain` (a different feature's own code)
  needing to know about staging environments, a cross-feature coupling
  judged not worth taking on for an edge case this narrow.
- Only one staging environment per source domain is supported (matches
  the goal's own "staging.{domain}" singular naming) — no support for
  multiple named staging environments per domain.

## Live verification result (2026-07-06): BLOCKED (environment, not a feature defect)
Ran `verify_phase7b_live.py staging`. The OLS config transaction failed at
its reload step (host's SIGUSR1 reload crash — see STATUS.md) while
provisioning the staging vhost, so the clone couldn't come up to be
curl'd. The transactional writer correctly detected the failure and
attempted rollback. Re-run once OLS reload is fixed on this box.

## UPDATE (2026-07-06, 2nd live run): provisioning PASSES; final WP install blocked (not a staging defect)
Supersedes the BLOCKED note above. With OLS reload fixed, staging's OLS vhost
provision + DB clone + wp-config rewrite all succeeded live. The final
`runuser -u <account> php <wp_install_helper>` step then failed for two
non-staging reasons: (a) INSTALL_HELPER_PATH resolved into the dev tree under
/root/ (unreadable by an account uid) because the verify run executes from
/root/cpanel-clone, not the deployed world-traversable /opt/forgehost the
code documents as its home; and (b) the same Phase 6b namespace-container PHP
breakage (see STATUS.md). Both are host/deployment issues, not staging code.

## FINAL (2026-07-06): staging PASSES live end-to-end
Supersedes both notes above. With account PHP serving (stale-namespace fix,
STATUS.md), a real source WordPress site installed and `create_staging` cloned
it to `staging.<domain>`: staged site served **HTTP 200** and
`/wp-admin/install.php` **HTTP 200** (wp-admin reachable), DB cloned as
`<user>_stg`. Done-When #6 met. The source-WP install helper is
`__file__`-relative; the dev-tree run needed `/root` traverse-only (`o+x`) for
the run, reverted to `700` immediately after (works unchanged from the
world-traversable `/opt/forgehost` deploy).
