# QA round 2 — Items 2 & 3 (xhigh effort): WordPress post-install management + multi-install/subdirectory support

## What existed already (per investigation, confirmed before writing code)

`daemon/wordpress.py` was installer-only (one-click install, async job
polling, `list_installs`) — no update/plugin/theme/search-replace/
maintenance/cache/password-reset logic. That logic already existed, fully
built and allowlisted, in `daemon/wpcli.py` (Phase 8 feature 8), but only
wired into an **account-level** page (`DevTools.jsx`'s WP-CLI tab), not the
per-domain `DomainDetail.jsx` WordPress tab the goal asks for. Both
`wordpress.py`'s `WordPressInstall` model (`domain` UNIQUE) and
`wpcli.py`'s detection (`docroot/wp-config.php` only) assumed exactly one
WordPress install living at a domain's docroot — no subdirectory support,
no multi-install tracking.

## Item 3: schema migration (done first — item 2's UI depends on it)

`WordPressInstall.domain` had a `UNIQUE INDEX` (`ix_wordpress_installs_domain`),
not a table-level constraint — confirmed by inspecting the actual generated
SQLite DDL, not assumed. That matters because SQLite can't `ALTER` a
constraint in place, but a separate index *is* droppable without a full
table rebuild. Migration (`shared/db.py`,
`_migrate_wordpress_installs_uniqueness`, called from
`_apply_additive_migrations`, which already runs at every daemon startup):
adds a new `path` column (additive, `TEXT NOT NULL DEFAULT ''`, via the
existing `_ADDITIVE_COLUMNS` mechanism), drops the old single-column unique
index, recreates a plain (non-unique) index of the same name for query
performance, and adds a new `UNIQUE INDEX` on `(domain, path)`. Idempotent
(safe on every startup) and a no-op for a fresh install (`create_all`
already emits the final model directly). 5 dedicated tests
(`tests/test_wordpress_migration.py`) simulate a real pre-existing
old-schema database with actual data in it and confirm: the column is
backfilled correctly, old data survives, the old unique constraint is gone,
the new composite one works (accepts same-domain-different-path, rejects
same-domain-same-path), and the whole migration is idempotent across
repeated runs.

`shared/models.py`: `WordPressInstall.domain` no longer unique;
`path: Mapped[str]` added; `__table_args__` now carries
`UniqueConstraint("domain", "path")`.

## Item 3: subdirectory installs + multi-install, backend

- `daemon/wordpress.py` `install()`: new optional `path` param. New
  `_install_target(docroot, path)` resolves the real install directory
  (docroot itself if `path` empty, else that subdirectory) with the same
  realpath-containment jail this module's own `_safe_extract_target`
  already uses for zip-slip defense (a crafted `path` like `../../etc`
  cannot escape the docroot). The empty-target check, wp-config.php
  overwrite check, extraction, chown, and `_run_silent_install` call all
  now target this resolved directory instead of always the domain's bare
  docroot. `site_url` (and therefore `admin_url`) correctly includes the
  subdirectory. The existing-install lookup is now keyed on
  `(domain, path)`, not `domain` alone, so a root install and a
  subdirectory install (or two different subdirectories) under the same
  domain coexist without colliding. `_allocate_database` takes an optional
  path-derived suffix hint (`wp_blog` for path `blog`) purely for
  admin-facing DB-list readability, falling back to the existing random-
  retry logic on any collision exactly as before.
- `daemon/wpcli.py`: new `_scan_wp_installs_under(docroot)` scans the
  docroot itself *and one level of subdirectories* for `wp-config.php`
  (deliberately not recursive further — unbounded recursion over an
  arbitrary customer tree is a real performance risk and unnecessary for
  the real-world subdirectory case). `detect_installs` now returns every
  install found this way, `id` staying exactly the bare domain for a root
  install (unchanged contract — every existing caller, e.g. the
  account-level DevTools tab, keeps working with zero changes) and becoming
  `"<domain>::<path>"` for a subdirectory one. Also gained an optional
  `domain` filter (scan just one domain instead of every domain on the
  account) for the new per-domain API route's efficiency. New
  `_resolve_install_dir(username, domain, path)` (with the same realpath
  jail) backs a `path`-aware `run_wpcli` — `_resolve_docroot` (the old,
  root-only resolver the account-level DevTools route still uses) is now a
  thin wrapper over it with `path=""`, so that existing route's behavior
  is provably unchanged.

## Item 2: per-domain WordPress management, API

`api/routers/wordpress.py` (domain-scoped router, unchanged prefix
`/api/v1/accounts/{u}/domains/{d}/wordpress`): `POST ""` (install) now
accepts an optional `path` in its body. Three new routes, all thin
`call_daemon` passthroughs to the *existing* `wpcli.*` ops (no new daemon
RPC ops registered — `wpcli.detect`/`wpcli.run`/`wpcli.get`/`wpcli.list`
already existed): `GET /installs` (live-detected installs at this domain,
root + subdirectories), `POST /actions` (run a WP-CLI action, `path`-scoped),
`GET /actions/runs` + `GET /actions/runs/{job_id}` (poll). The pre-existing
account-level DevTools route (`api/routers/devtools.py`) is untouched.

## Item 2: per-domain WordPress management, frontend

`frontend/src/pages/customer/DomainDetail.jsx`'s `WordPressTab` (previously
installer-only) now: queries `GET .../installs`; if none are detected,
shows the install form (unchanged UX, plus a new optional "Subdirectory"
field); if one or more are detected, renders a `WordPressInstallCard` per
install (admin URL, WP version, install location) with the full WP-CLI
action set behind a "Manage" toggle (update core/plugins/themes, activate/
deactivate, reset admin password, cache flush, maintenance mode on/off,
search-replace with dry-run preview) plus a live command-output panel — and
an "Install another WordPress" button that reopens the install form
(pre-flagged as a subdirectory install) so a domain can accumulate several
independently-managed installs over time. The `WP_ACTIONS` list and run/
poll UI directly mirror `DevTools.jsx`'s existing `WpCliTab` (same backend,
same allowlisted action set) rather than reinventing it.

## Tests

`tests/test_wordpress_migration.py` (new, 5 tests): schema migration, see
above. `tests/test_wordpress.py` (+11): `_install_target` jail/traversal
rejection, `_suffix_hint` derivation, a subdirectory install end-to-end
(files land in the right place, DB name reflects the path, root itself
untouched), root+subdirectory coexisting with distinct DB names, duplicate-
at-same-path rejection while a different path still succeeds, non-empty-
target refusal scoped to the subdirectory (not the whole docroot), and
path-traversal rejection. `tests/test_wpcli.py` (+7): subdirectory
detection (with and without a root install present alongside it), hidden-
directory exclusion, domain-scoped `detect`, `run_wpcli` targeting a
subdirectory, path-traversal rejection, and an explicit backward-
compatibility test that omitting `path` entirely (every pre-existing
caller) still resolves to the docroot exactly as before this item.

`python3 -m pytest tests/test_wordpress.py tests/test_wordpress_migration.py tests/test_wpcli.py tests/test_handlers_database.py -q`
→ 59 passed, 0 failed.

`npm run build` — clean (`DomainDetail` chunk grew from ~55.7 kB to
~62.6 kB, the new management UI).

## What's still open

Live click-through (install a real WordPress subdirectory site, run a real
WP-CLI action against it, confirm admin URL/version render correctly in the
new card) needs a disposable hosting account — deferred to the operator
post-deploy, same reasoning as every other live-mutation-requiring check in
this batch (see `docs/CHECKPOINT-qa2-4-ftp-firewall.md`'s equivalent note).
The underlying WP-CLI execution path itself (`daemon/wpcli.py`'s allowlisted
argv builder, `cmdjobs.submit`'s `runuser`-as-account-user wrapping) is
unchanged, pre-existing, and was already live-verified in Phase 8
(`docs/CHECKPOINT-phase8-8-wpcli.md`) — only the new
detection/resolution/routing layer around it is new here.
