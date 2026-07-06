# Phase 7a Feature 2: Python app hosting

## What was built

Per-account Python WSGI/ASGI app hosting — same lifecycle/isolation shape as
feature 1 (`daemon/pythonapps.py`, sharing `daemon/appunits.py`/
`daemon/appcrypto.py`/`daemon/portalloc.py`), with two concrete differences:

- **Per-app virtualenv**, always under the account's own home
  (`~/pythonapps/<name>/venv`), created via `python3 -m venv` as the
  account's own uid (`runuser -u <username>`, never root). `gunicorn` +
  `uvicorn` are installed into every app's venv at `create()` time
  (regardless of `app_type`, so switching a running app between WSGI/ASGI
  later never requires recreating the venv) — the customer's *own*
  application dependencies (`requirements.txt`) are a separate, explicit
  `apps.python.pip_install` action, same "don't run an implicit install of
  customer-supplied code" posture as `nodeapps.npm_install`.
- **Launch command chosen by `app_type`**: `wsgi` → `gunicorn --bind
  127.0.0.1:<port> <entry_point>`; `asgi` → `uvicorn <entry_point> --host
  127.0.0.1 --port <port>`. `entry_point` is validated as gunicorn/uvicorn's
  own `module:callable` syntax (`shared/validation.py`'s
  `validate_python_entry_point`).

Everything else — 1:1 domain binding + OLS reverse proxy via the same `type
proxy` extProcessor/Proxy-Context mechanism, `Slice=`-based cgroup
assignment, `Restart=on-failure`, encrypted-at-rest env vars via a root-only
`EnvironmentFile`, logs to `~/logs/python/<name>.log` (never journald),
API/UI at `/accounts/{u}/apps/python`, and account-termination cleanup — is
identical in shape to feature 1; see `CHECKPOINT-phase7a-1-nodejs.md` for the
shared reasoning, not repeated here.

## Real bugs found and fixed

Both real bugs found while building/verifying feature 1 were fixed here
*before* this feature's own live test ever hit them (same code shape, same
failure class):

1. The "failed OLS apply must delete the already-committed DB row + unit
   file" compensation (`create_app`'s `try/except`) was written into
   `pythonapps.py` from the start, mirroring `nodeapps.py`'s fix.
2. `enabled=False` at `create()` time (not `True`) for the identical
   "create() doesn't start the unit, so the desired-state flag shouldn't
   claim it's running" reason.

No *new* bug specific to the Python path was found during this feature's own
live verification beyond what feature 1 already surfaced and fixed.

## Live verification (real, on this VM)

Reused the same disposable test account (`p7anodetest`) with a second
domain (`p7apytest.104-234-179-64.sslip.io`):

- Real FastAPI app (`main.py` + `requirements.txt` declaring `fastapi`)
  written to `~/pythonapps/myfastapi/`. Venv + gunicorn/uvicorn install
  happened automatically at `apps.python.create` time; the app's own
  `fastapi` dependency was installed via the real `apps.python.pip_install`
  action.
- Started via `apps.python.start` — succeeded on the **first** attempt (no
  repeat of feature 1's transient `219/CGROUP` retry), confirmed via
  `systemctl status`: real process
  (`/home/p7anodetest/pythonapps/myfastapi/venv/bin/python3 .../uvicorn
  main:app --host 127.0.0.1 --port 30001`), real cgroup
  (`/forgehost.slice/forgehost-p7anodetest.slice/forgehost-python-p7anodetest-1.service`).
- `curl` against the real public domain
  (`https://p7apytest.104-234-179-64.sslip.io/`) returned the FastAPI app's
  real JSON response, including its own env var
  (`GREETING=hello-from-fastapi`) round-tripped through the Fernet-encrypted
  storage exactly as with the NodeJS app.
- Cleaned up by the same `account.terminate` call verified in feature 1's
  checkpoint (unit, env file, DB row, venv-owning home dir all removed
  together).

## What's untested / deferred

- Only `gunicorn`/`uvicorn` were exercised (no `daphne`/`hypercorn` — not
  requested by the goal, and the goal explicitly names only these two).
- A WSGI (`gunicorn`, Flask-style) app was not independently
  live-verified end-to-end this pass — only the ASGI/`uvicorn`/FastAPI path
  was (the goal's own Definition of Done names "a real FastAPI app", ASGI).
  The `_exec_start` command construction for `wsgi` is unit-tested
  (`tests/test_pythonapps.py::test_unit_execstart_uses_gunicorn_for_wsgi`)
  but not exercised against a real running Flask app on this server.
