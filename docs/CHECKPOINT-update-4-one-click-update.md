# CHECKPOINT — Panel Update System, feature 4: one-click update

Date: 2026-07-10. Async update job (daemon/updates.py) + detached
finalizer (scripts/update_finalize.py) + API start/status/log.

## Architecture decision — the two-process split (load-bearing)

The daemon does every step that is safe while current code runs:
pre-flight → backup → download → checksum → staged extract → venv →
migrations. The dangerous window — symlink swap, restarting
forgehost-provisiond **(the process running the job)** + forgehost-api,
health check, swap-back on failure — is handed to
`scripts/update_finalize.py`, launched via `systemd-run --collect` from a
copy staged next to the control-plane DB, interpreted by the SYSTEM
python3, importing **only the stdlib**. Rationale: (1) an in-daemon
restart would kill the job mid-swap (daemon/servicemgr.py's allowlist
already refuses the panel's own units for exactly this reason); (2) the
rollback machinery must keep working when the new payload is broken —
it cannot depend on the new venv or any forgehost import.

## Step-by-step (goal 4a–k → implementation)

- a **Pre-flight**: live dir exists; ≥2GB free on /opt and the DB volume;
  `update_preflight_tests` (default on) runs the LIVE install's own pytest
  suite (tests/ ship in the tarball for exactly this) — non-zero exit
  aborts with the output tail in the step log.
- b **Backup** to `/var/backups/forgehost/pre-update-{ver}-{ts}/` (0700):
  panel DB via sqlite3's online-backup API (consistent under WAL with
  live writers, unlike `cp`), plus `/etc/forgehost` copytree.
- c **Download**: only from `https://github.com/{repo}/releases/download/`.
  **Documented deviation**: the goal says "no redirect following", but
  GitHub serves ALL release assets via 302 to `*.githubusercontent.com` —
  literal compliance cannot download anything. The security intent is
  enforced instead: manual redirect loop (httpx follow_redirects=False),
  every hop must be https on github.com/*.githubusercontent.com, ≤4 hops,
  hard size cap (500MB default).
- d **SHA256** of the tarball vs the `.sha256` asset, verified BEFORE any
  extraction; mismatch/malformed → download deleted, job aborted.
- e **Staged extract** to `/opt/forgehost-{ver}/`: every member validated
  first (absolute paths, `..` components, members outside the version
  prefix, symlinks, hardlinks, devices/fifos, declared-size bomb → all
  rejected), then extracted with Python 3.12's `filter="data"` as a
  second layer, into a same-filesystem temp dir, atomically renamed into
  place. Refuses to touch a dir that is the live symlink's target.
  Then `python3 -m venv` + `pip install -r requirements.txt` inside the
  staged tree (units exec `/opt/forgehost/.venv/...`, which resolves
  through the symlink after the swap — no unit edits).
- f **Migrations**: the NEW tree's venv runs `init_db()` (create_all +
  additive columns — additive-only by project convention, so old code
  still runs post-rollback) against the live DB, before the swap.
- g **Atomic swap**: temp symlink + `os.rename` (rename-over-symlink is
  atomic). **First-run conversion**: `/opt/forgehost` is today a real
  directory — the finalizer moves it to `/opt/forgehost-{from_version}`
  and creates the symlink (services follow the inode until restarted
  seconds later). install.sh continues to install the plain dir; the
  first update converts.
- h **Restarts**: `systemctl restart forgehost-provisiond` then
  `forgehost-api` — the ONLY services touched. OLS + hosted sites never.
- i **Health check**: HTTPS GET `/healthz` on the API + a real
  length-prefixed JSON RPC round trip on the daemon socket (ANY
  well-formed reply counts — a rolled-back-to version may predate the
  update.* ops). 90s deadline, 2s poll.
- j **On failure**: swap back, restart again, re-health-check, job marked
  failed + rolled_back, admin emailed (smtplib to localhost, resolved
  sender/recipient passed in as args by the daemon while real code was
  still running). Exit 2 (and an explicit manual-fix email) if even the
  swap-back fails.
- k **Cleanup**: daily cron prunes `/opt/forgehost-X.Y.Z` dirs older than
  `update_keep_old_days` (3). Guards, in order: strict
  `^forgehost-\d+\.\d+\.\d+$` basename regex (can NEVER match
  /opt/forgehost-nodejs or forgehost.pre-filebrowser — tested), real
  non-symlink dir, not the live target, older than the window.

Every step lands in the job's `steps` JSON (UpdateJob table) AND as a JSON
line in `/var/log/forgehost/updates.log` (audit.py's account-events
pattern). No secrets ever enter either (static messages, paths, versions,
sizes; procutil already redacts argv logging).

State machine: `pending → running → finalizing → completed | failed`;
`finalizing` marks the handoff — the finalizer writes the terminal state
directly via sqlite3 in the exact datetime text format SQLAlchemy writes
(ORM read-back is tested).

## API

`POST /api/v1/admin/update/start {confirm, totp_code?, to_version?}` —
require_admin + explicit confirm + browser-session-only (API tokens have
panel_user_id=-1 and can't satisfy TOTP) + **2FA confirmation when the
admin has TOTP enabled** (`totp.status` → `totp.check_login_code`, the
first sensitive-action re-auth gate in the codebase). `GET
/admin/update/status`, `GET /admin/update/log?job_id=`.

## Tests

47 daemon tests + 10 API tests + 5 finalizer integration tests. The
finalizer ones run the REAL script (a tmp copy — it self-deletes on
success) against real /tmp dirs with a fake systemctl, a real local HTTP
health endpoint and a real RPC-framing Unix socket: success swap, failed
health → automatic swap-back (+ rolled_back recorded), dir→symlink
conversion, rollback mode, non-symlink refusal. Never `/opt/forgehost`
(goal rule honored). The full-job stitch test drives
start_update→finalizing over a mocked transport with real extraction and
asserts the handoff argv, staged tree, and backup contents.

## Honestly open

- A real end-to-end update on THIS box (real systemd units, real GitHub
  release) is impossible until a GitHub repo + release exist (no remote,
  no gh) and would restart the production panel — operator-gated. The
  finalizer sandbox tests are the honest stand-in.
- pip install in the staged venv needs PyPI network access at update
  time; a failure aborts cleanly pre-swap (tested only via mocked run).
