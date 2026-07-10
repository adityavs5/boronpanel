# CHECKPOINT run-a-7 — Request logging + rotation

**Goal:** structured JSON per request (timestamp, method, path, status,
duration_ms, user, IP). Log to `/var/log/forgehost/api-access.log`.
Logrotate: daily, 30 days, compressed. Separate 5xx error log. Admin UI:
last 100 errors visible. API: `GET /admin/logs/errors`.

## What was built

- **`api/logsetup.py`** (split out and unit-tested like
  `daemon/logsetup.py`): builds the JSON record and owns two append-only
  loggers — `forgehost.access` → `api-access.log` (every request),
  `forgehost.access.error` → `api-error.log` (5xx only, the separate error
  stream). `build_record()` is pure (asserted directly in tests). Lazy
  first-use configuration reads `settings.log_dir` at runtime so tests can
  repoint it; `reset()` is the test hook. **Robustness rule: logging never
  takes the API down** — an unwritable log dir falls back to a NullHandler
  and warns once, rather than raising on a real request.
- **Middleware** in `api/main.py` (`_access_log`), registered LAST so it is
  the **outermost** middleware: its `duration_ms` covers the whole stack
  (rate-limit + whitelist + headers + route), and it records the final
  status of *every* response — including a rate-limit 429 or whitelist 403
  that never reached a route. It also wraps `call_next` in try/except:
  an unhandled exception is turned into a 500 by Starlette's error handler
  which sits *outside* this middleware, so without catching here a genuine
  crash would never reach the error log — it's recorded as a 500 and
  re-raised so the real 500 response is still produced.
- **`user` with no extra work**: `get_identity` now stashes the resolved
  identity on `request.state.identity`; the middleware reads
  `request.state.identity.username` (populated by the route's dependency,
  which has already run by the time the outermost middleware regains
  control). No second auth resolution, no DB read on the log path;
  unauthenticated requests log `user: null`.
- **`api/routers/adminlogs.py`**: `GET /api/v1/admin/logs/errors?limit=N`
  (admin-only, clamped 1–100). Reads `api-error.log` directly — the file
  is written by this same forgehost-api process, so no daemon RPC is
  needed. `tail_error_records()` reads only the last 256 KiB from the end
  (the file is unbounded between logrotate runs), drops the partial first
  line left by the seek cut, skips malformed lines, returns newest-first.
- **`deploy/forgehost-api.logrotate`**: daily, rotate 30, compress,
  covering both files. `copytruncate` (not rename+create) because the
  Python `FileHandler` holds the fd open for the process's life — a
  rename would leave the daemon writing to the rotated file. The
  copytruncate race window is the documented trade against making the app
  handle SIGHUP. The installer (feature 9) installs this to
  `/etc/logrotate.d/` and creates `/var/log/forgehost` group-writable by
  `forgehost-api` so the unprivileged API can create these two files
  (the root daemon's `daemon.log` shares the dir).

## Log-directory ownership (the one deployment prerequisite)

`/var/log/forgehost` was previously written only by root processes
(daemon.log + the root cron scripts). For the unprivileged forgehost-api
to write `api-access.log`/`api-error.log` there, the directory must be
group-writable by `forgehost-api`. Feature 9's installer creates it
`root:forgehost-api`, mode `2775` (setgid so new files inherit the group).
On a box where that hasn't happened yet, the graceful fallback keeps the
API serving (logging disabled + one warning) rather than 500-ing.

## Tests

`tests/test_request_logging.py` — 14 tests: record shape (all 7 fields,
duration rounding, ISO timestamp, null user); a 200 writes only to access,
a 5xx writes to both, a 4xx never to error; **unwritable log dir doesn't
raise**; tail reader (empty when absent, newest-first + limit, skips
malformed lines, **drops the partial first line after a >256KiB seek
cut**); admin endpoint returns recent errors, requires admin (403 for
customer, 401 unauthenticated); and an end-to-end real-ASGI-stack test
that a live `/healthz` request writes an access line with the true status.

## Frontend

Admin **Error Log** page (`frontend/src/pages/admin/ErrorLog.jsx`, nav
under "Security & Logs", route `/error-log`): a newest-first table of the
last 100 5xx responses (time, status, method, path, user, IP, duration) —
a thin React-Query read of `GET /admin/logs/errors`, same table/state
pattern as AuditLog. Empty state reads "No recent errors — that's a good
sign."

## What's honestly still open

- Not live-verified against `/opt/forgehost` (deploy needs approval);
  backend, tests, and frontend build are green locally.
