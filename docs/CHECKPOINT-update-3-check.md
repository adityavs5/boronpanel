# CHECKPOINT — Panel Update System, feature 3: update check

Date: 2026-07-10. Daemon op `update.check` + 1h cache + daily cron with
admin email + admin API.

## What was built

- **`daemon/updates.py`** — `check(params)` fetches
  `api.github.com/repos/{update_github_repo}/releases/latest` (httpx,
  `_transport` test hook per daemon/cloudflare.py's convention, optional
  `GITHUB_TOKEN` from secrets.env for rate limits), normalizes `tag_name`
  through a strict `x.y.z` parser, and returns
  current/latest/update_available/changelog_url. Results (including
  **failures**) are cached in the single-row `UpdateState` table for
  `update_check_cache_seconds` (1h) — a misconfigured repo must not make
  dashboard polling hammer GitHub. `force=true` (Check-now button, daily
  cron) bypasses.
- **Asset URL validation at check time**: a release whose
  `forgehost-X.Y.Z.tar.gz` / `.sha256` assets aren't under
  `https://github.com/{repo}/releases/download/` is rejected outright.
- **Unconfigured ≠ error**: empty `update_github_repo` (this repo's
  current reality — no git remote exists) reports `configured: false` so
  the UI renders a setup hint.
- **Ops registered** (`daemon/server.py`): `update.check/.status/.start/
  .rollback/.history/.log/.cleanup`; check/status/history/log on the
  REPORTING executor (network + dashboard-polled, same reasoning as
  cf.health).
- **Cron**: `scripts/update_check.py` (monitoring_check.py bootstrap) +
  `deploy/forgehost-update.cron` (daily 05:25) installed by install.sh
  (+ uninstall). One pass = forced check, admin email via
  `notifications._send_email` to `MonitoringSettings.admin_email`
  (**deduped per release** via `UpdateState.last_notified_version` — one
  email per new version, not one per day forever), and old-version-dir
  pruning (feature 4k).
- **API**: `GET /api/v1/admin/update/status` (cached check + job/rollback
  state), `POST /api/v1/admin/update/check` (force). Admin-only.

## Tests (in tests/test_updates.py, test_update_api.py)

check: unconfigured / fetch+compare / cache hit counting / force bypass /
equal-version-not-available / 404-error cached and surfaced / off-GitHub
asset URL refused. Version parser: v-prefix, numeric (not lexicographic)
compare, garbage rejected. notify: emails once per release, skips without
recipient. API: admin-only, force flag plumbed.
