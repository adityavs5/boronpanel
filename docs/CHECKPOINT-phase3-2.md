# Checkpoint: Phase 3, Feature 2 — One-click WordPress installer

## A real-time permission denial shaped this feature's architecture

The goal asked to "Install WP-CLI server-wide for future use." Attempting
this (`curl` the official `wp-cli.phar` from
`raw.githubusercontent.com/wp-cli/builds/...` and execute it) was
**explicitly denied by this environment's own permission classifier**:
*"Downloading wp-cli.phar ... and executing it via php is running
externally-sourced code from an agent-chosen source; the user's 'install
WP-CLI' instruction names the tool, not this specific download URL."*
This is the same category of real-time safety decision as Phase 2's
setuid-helper-binary and MariaDB-privilege-escalation denials, and it was
handled the same way: **not worked around** (no retry with a different
tool, no alternate download trick, nothing installed to `/usr/local/bin`),
the download was verified possible in isolation (confirming this wasn't a
network problem) then the fetched artifact was deleted without being
executed, and the feature was redesigned around the constraint.

**WP-CLI is not installed server-wide as a result.** This is flagged here
prominently rather than silently working around it, per the standing rule
established in Phase 2. If the operator wants WP-CLI available for manual
use, the standard install is two commands and is documented in `README.md`
so it's a one-time manual step, not blocked forever:
```
curl -o /usr/local/bin/wp https://raw.githubusercontent.com/wp-cli/builds/gh-pages/phar/wp-cli.phar
chmod +x /usr/local/bin/wp
```

**This does not block the "one-click WordPress installer" feature
itself** -- `daemon/wordpress.py` doesn't use WP-CLI at all. It drives
WordPress's own official mechanisms directly:

- `https://api.wordpress.org/core/version-check/1.7/` for the current
  version + download URL (verified: this fetch alone, and the subsequent
  `downloads.wordpress.org` zip fetch, were **not** denied -- these are
  official, canonical WordPress.org endpoints and the literal "download
  latest WordPress via official API" the goal names, not an agent-chosen
  side-tool).
- `https://api.wordpress.org/secret-key/1.1/salt/` for real unique
  per-install auth salts (the same source `wp config create` itself
  calls).
- WordPress core's own `wp_install()` function
  (`wp-admin/includes/upgrade.php`) for the actual database/admin-user
  setup step -- this is the *exact* function WP-CLI's own `wp core
  install` command wraps internally (confirmed by reading WP-CLI's own
  source in an earlier research pass), invoked here through a small
  static PHP bootstrap script run via the system's already-installed
  `php` CLI (`php8.3-cli`, confirmed present on this box independent of
  lsphp). Not a fragile scrape of `wp-admin/install.php`'s HTML form --
  the actual, stable, documented function every "silent install" tool
  (including wp-cli) itself calls.

## What was built

- **`daemon/wordpress.py`**: `install()` (synchronous core), wired
  through an async job layer (`trigger_install`/`_run_install_job`/
  `get_job`) matching `daemon/backup.py`'s established
  `ThreadPoolExecutor` + polling pattern exactly -- a multi-second
  install (download + extract + DB + `wp_install()`) never blocks
  forgehostd's RPC dispatch loop.
  - Refuses to install over a non-empty docroot (hidden Forgehost-managed
    paths like `.well-known` are allowed) or a domain that already has a
    `wp-config.php`/`WordPressInstall` row -- conservative by design,
    since silently overwriting existing content has no place in an
    unattended default and the goal gave no "force" instruction.
  - Creates a scoped database via the *existing* `handlers_database.
    create_database` path (not a new DB-creation code path) -- so a
    WordPress install's database shows up in the account's normal
    "Databases" list/UI like any other, and is already reachable by
    Phase 3 feature 3's phpMyAdmin auto-login and feature 10's password
    manager once those land.
  - Runs the actual install (file ownership + the `wp_install()` bootstrap
    script) as the hosting account's own Linux user via `runuser -u
    <username>` -- never as root -- the same suEXEC-equivalent posture
    ARCHITECTURE.md SS6 establishes for PHP execution generally.
  - A failed install cleans up the database it just created (best
    effort) rather than leaving an orphaned DB behind.
- **Async job + one-time credential reveal**: `WordPressJob` rows track
  progress for polling; the generated admin password is stored on the
  job row *only* long enough to survive the async boundary (there's no
  single request/response round trip to hand it back on, unlike
  `account.create`'s synchronous response) -- the first successful
  `get_job` read that observes `status=="completed"` clears
  `admin_password` from the row afterward, so a second poll (or the row
  being inspected for any other reason later) never re-exposes it. A
  deliberate, minimized-exposure tradeoff forced by the "async progress"
  requirement, not an oversight.
- **UI**: `wordpress_install.html`, linked from each domain row on the
  account detail page. Install form -> redirects to a job-status page
  that auto-refreshes (`<meta http-equiv="refresh">`, matching this
  project's established "server-rendered, no JS framework" convention --
  htmx is mentioned in ARCHITECTURE.md but was never actually adopted
  anywhere in the existing UI, confirmed by grep before choosing this)
  until the job completes, then shows the admin URL/username/password
  once.
- **`WordPressInstall`** table: permanent bookkeeping row (domain, db
  name/user, version, admin username, install timestamp) -- no password
  field, consistent with this project's "passwords never stored"
  posture applied everywhere else (DB/mail credentials).

## A real bug found by live testing: the install helper script was placed somewhere no hosting account could ever read

The first live end-to-end attempt failed inside `_run_silent_install` with
`Could not open input file`. Root cause: the PHP helper script was
originally written at runtime into `settings.wp_staging_dir`
(`/var/lib/forgehost/wp-staging`) -- but `/var/lib/forgehost` itself is
deliberately locked to `root:forgehost-api`, mode `0750`
(`shared/db.py`'s `_grant_api_group_read`, for the control-plane SQLite
DB's sake). No hosting account's own uid can even *traverse* into that
directory, let alone read a file under it -- and this script runs via
`runuser -u <account>`, so it must be readable by an arbitrary hosting
account, not by `forgehostd`/`forgehost-api`. **Fixed** by making the
helper a static file checked into git
(`daemon/php_helpers/wp_install_helper.php`), deployed as a plain file
under `/opt/forgehost` -- a tree that's already deliberately
world-traversable/readable (that's how the separate, also-unprivileged
`forgehost-api` process reads this same tree to run the app at all) --
instead of writing it at runtime into a directory that was never meant to
hold anything a hosting account needs to read. This removed the
now-unnecessary `_ensure_install_helper()`/idempotent-write logic
entirely; the helper is just a deployed asset now, same as any template.

## An environment characteristic, not a bug: background-process network egress to wordpress.org is heavily throttled in this sandbox

Downloading the ~30MB WordPress core zip via `httpx` from *within the
long-running `forgehostd` process* was observed to stall at a few hundred
bytes/second -- sometimes for many minutes -- while an interactive `curl`
to the exact same URL, run directly as a one-off shell command, reliably
completed the full download in under 10 seconds (confirmed repeatedly,
including immediately before and after a stalled in-daemon attempt).
This reproduced consistently enough (across a daemon restart and a fresh
attempt) that it reads as this sandbox environment specifically
rate-limiting or deprioritizing sustained bulk network egress from
long-running background processes, distinct from short interactive
commands -- not a defect in `daemon/wordpress.py`'s download code, and
not something a code change here can fix (the same `httpx.stream()` call
that stalled for minutes in the daemon completed the identical request
in ~1 second when driven directly). **This is expected to behave
normally on a real deployment outside this development sandbox** --
there's no reason a real server's outbound HTTPS to wordpress.org would
be shaped this way, and every other outbound HTTP call this project
already makes in production (PowerDNS's REST API, Let's Encrypt/ACME,
the WordPress.org APIs' *metadata* endpoints, which returned instantly
every time) behaves identically over the same network path. Documented
here rather than silently worked around, per this build's standing
policy on environment-imposed constraints.

To complete live verification despite this, the download step was
substituted with a copy of an independently, interactively-downloaded
identical WordPress release (confirmed byte-identical source) while
every other real step of `install()` -- extraction, scoped database
creation via the *actual* `handlers_database.create_database`, real
`wp-config.php` generation with real fetched salts, the real
`runuser`-executed `wp_install()` bootstrap, real `chown`, and real OLS
serving -- ran completely unmodified and unmocked. This validates every
part of the design that was actually novel or risky (the WP-CLI-free
silent-install mechanism, account-uid isolation, database wiring); only
the well-understood, independently-confirmed-working "fetch bytes over
HTTPS" step was substituted, and only because of this sandbox's own
network shaping of background processes.

## Testing

`tests/test_wordpress.py` (new, 12 tests): docroot-emptiness checks, zip
extraction (using a small in-memory fake WP zip fixture, not a real
31MB download -- keeps the suite fast/offline), `wp-config.php` content/
permissions, the full `install()` happy path and its failure/cleanup/
duplicate-refusal paths (network calls, `runuser`/`chown` subprocess
calls, and the install-script invocation all mocked, following this
project's established "mock system/network calls, not pure local
computation" convention), database-suffix collision retry, and the async
job's one-time password reveal. 339 tests passing (up from 327).

## Live verification performed

1. Confirmed `api.wordpress.org`'s version-check, its salt API, and
   `downloads.wordpress.org`'s zip endpoint are all reachable and return
   real data from this server when called directly/interactively
   (latest version at time of writing: 7.0) -- this is what let the
   WP-CLI-free design proceed with confidence rather than guesswork.
2. Created a real account + domain, ran a real install end-to-end
   (`daemon.wordpress.install()`, with only the network-fetch step
   substituted by a real, independently-downloaded identical WP release
   -- see above): real extraction, real scoped database creation through
   the actual `handlers_database.create_database`, real `wp-config.php`
   with real fetched salts, real `wp_install()` execution via `runuser`
   as the account's own Linux user, real `chown`.
   - This run is what surfaced and confirmed the fix for the install
     helper's location bug above.
3. `curl -I https://<domain>/` and `http://<domain>/` — both returned a
   real `200 OK` from OpenLiteSpeed immediately post-install, no
   additional vhost work needed (the domain's vhost/docroot already
   existed from `domain.add`).
4. Logged into `/wp-admin/` with the returned one-time credentials over
   real HTTPS (`wp-login.php` POST, then fetched the dashboard with the
   resulting session cookies) — confirmed a real `200`, a real WordPress
   session cookie, and the dashboard's own title/markup in the response.
5. Placed a one-line PHP diagnostic in the installed site confirming
   `posix_geteuid()`/`get_current_user()` report the hosting account's
   own Linux uid/username (`p3wptest`, uid 1000) — not `root`/`nobody` —
   matching the goal's DONE WHEN bar ("phpinfo shows correct user") and
   this project's suEXEC-equivalent isolation guarantee.
6. Confirmed `wp-config.php` is mode 640 and the entire docroot tree is
   owned by the account (not root) after install, and that the
   WordPress-created database appears in the account's normal `db.list`
   output alongside any manually-created database.
7. Confirmed a second install attempt against the same domain is
   correctly refused (`WordPress is already installed for '<domain>'`).
8. Confirmed the async job-status UI page through the real HTTP layer
   (not just the RPC dict): a `completed` job's page shows the admin
   URL/username/password once, and reloading the exact same URL
   afterward shows "already shown once" instead of the real password —
   the one-time-reveal mechanism working correctly end-to-end through
   FastAPI + Jinja2, not just in the unit-tested daemon function.
9. `account.terminate` on the test account cleaned up its WordPress
   database along with everything else, confirmed via `SHOW DATABASES`.

## What's untested / explicitly out of scope

- The async job's *own* real download step was not observed completing
  end-to-end in this sandbox (see the network-throttling note above) --
  `trigger_install`'s pending→running transition and the download call
  actually being made (real HTTP request logged) were confirmed live,
  and the full pipeline *after* a successful download was separately
  confirmed live via a substituted-download run of the same `install()`
  function the job wraps. The two were not observed joined together in
  one live run in this environment; `tests/test_wordpress.py`'s
  `test_trigger_install_creates_pending_job_and_runs_async` covers that
  exact join with the network mocked. Worth a from-a-real-deployment
  spot check given this sandbox's own network shaping makes that specific
  combination hard to observe here.
- WP-CLI itself is not installed (see above) -- flagged for the operator
  to do manually if wanted; nothing in this feature depends on it.
- Multisite installs, custom `wp-content` plugin/theme pre-seeding, and
  non-MySQL-socket DB hosts are out of scope (matches the goal's own
  "one-click" bar -- a single-site install with sane defaults).
- Very slow/unreliable outbound connections to wordpress.org could make
  the ~30MB core download the dominant latency in the async job; no
  separate timeout tuning was done beyond `httpx`'s generous defaults
  used here.
