# CHECKPOINT — Panel Update System, feature 1: version tracking

Date: 2026-07-10. Goal: `version.py` with `FORGEHOST_VERSION` as the single
source of truth, shown in sidebar footer, admin dashboard, `GET
/api/v1/version`, and installer output.

## What was built

- **`version.py`** (repo root) — `FORGEHOST_VERSION = "1.0.0"`. Repo root
  rather than `shared/` because every consumer already has the repo root on
  its import path (`pytest.ini pythonpath=.`, systemd `PYTHONPATH=
  /opt/forgehost`), and a dependency-free top-level module is readable by
  the stdlib-only update finalizer (feature 4) and by `sed` from shell
  scripts without importing the app.
- **`GET /api/v1/version`** (`api/routers/update.py`, registered in
  `api/main.py`) — returns `{"version": FORGEHOST_VERSION}`.
  **Decision: authenticated (any role), not public.** The sidebar footer is
  post-login for both roles; advertising the exact version to anonymous
  scanners aids exploit targeting for zero benefit. The pre-login page shows
  the build-time version baked into the SPA bundle instead (ships from the
  same version.py at release time, so it can't drift in a proper release).
- **Sidebar footer** (`frontend/src/components/layout/Sidebar.jsx` +
  new `frontend/src/hooks/useVersion.js`) — `{panelName} v{version}` at the
  bottom of the sidebar, hidden when collapsed (same rule as the Collapse
  label). Value comes from `GET /api/v1/version` (authoritative — it's what
  is actually deployed), falling back to the build-time constant. The old
  header line `Admin · v1.0` no longer shows a version (footer owns it now;
  header keeps the role label).
- **Build-time injection** (`frontend/vite.config.js`) — reads
  `../version.py` at build time and defines `__FORGEHOST_VERSION__`;
  `src/config/constants.js`'s `APP_VERSION` (used by the login page) now
  derives from it instead of a hardcoded `'v1.0'`.
- **Installer output** (`scripts/install.sh`) — parses version.py with sed
  near the constants block; header line is now `Forgehost installer vX.Y.Z`
  and the success line `Done. Forgehost vX.Y.Z -- Panel: ...`.
  **Drive-by fix, disclosed:** the header previously used `${DRY_RUN:+...}`,
  which expands whenever the variable is non-empty — and it is always the
  non-empty string `"true"`/`"false"`, so *every* run printed `(dry-run)`.
  Replaced with a real `$DRY_RUN &&` test while editing that exact line.
- "Admin dashboard" display lands with the update banner/card in features
  3/7 (the banner carries current→latest); the sidebar footer is already
  visible on the dashboard meanwhile.

## Tests

`tests/test_update_version.py` (6 tests): semver format pin; endpoint
returns the constant; visible to customer role; **401 unauthenticated**
(pins the deliberate not-public decision); the installer's exact sed
expression extracts the version; the vite regex matches version.py's actual
format. Plus `tests/test_installer.py` (6) re-run green — shellcheck still
clean and `--dry-run` still exits 0 after the install.sh edits.

## Honest notes

- `frontend/package.json` still says `"version": "1.0.0"` — npm metadata,
  not displayed anywhere; release.sh (feature 2) will keep it in sync at
  bump time so nothing drifts.
- The SPA hasn't been rebuilt yet — `static/dist` still carries the old
  bundle; a single rebuild happens once the sidebar badge + Updates page
  (features 3/7) are in, then gets QA'd with the puppeteer rig.
