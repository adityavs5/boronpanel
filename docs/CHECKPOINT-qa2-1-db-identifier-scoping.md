# QA round 2 — Bugs 4 & 5: phpMyAdmin "not found" + DB password reset "database not found"

Root-caused (not just patched) per the goal's explicit instruction. Both bugs
shared one underlying defect class.

## Bug 5: DB password reset / delete — "database not found"

**Root cause**: `daemon/handlers_database.py`'s `change_password()`/
`drop_database()` treated the `name` RPC param as a *bare suffix* and always
re-prefixed it via `_scoped_name(username, suffix)` → `f"{username}_{suffix}"`.
The React `Databases.jsx` page, however, only ever has the row's already-
fully-qualified `db_name` back from `db.list` (e.g. `demo1_shop`) — it has no
bare suffix to send. Passing the full name through `_scoped_name` double-
prefixed it (`demo1_shop` → `demo1_demo1_shop`), which matched no
`DatabaseGrant` row, raising exactly the reported `RuntimeError("database
'demo1_demo1_shop' not found for account 'demo1'")`.

`create_database()` was unaffected — its dialog explicitly collects a bare
suffix and documents it ("prefixed with your username").

**Fix**: added `_resolve_existing_db_name(username, name)` — tolerant of
*either* a bare suffix or an already-scoped full name (only used for
lookups of something that must already exist; `create_database` keeps
calling `_scoped_name` directly, unconditionally, so a legitimately
suffix-starting-with-username create request is never reinterpreted).
`drop_database`/`change_password` now call the tolerant resolver.

## Bug 4: phpMyAdmin "not found" / auto-login doesn't work

**Root cause, part 1 (dead route)**: `frontend/src/pages/customer/
Databases.jsx` called `window.open('/pma/${username}', '_blank')` — no such
route exists anywhere (frontend router or backend), so it rendered the SPA's
"Not Found" page. The real, working mechanism
(`POST /api/v1/accounts/{u}/databases/{name}/pma-token` →
`daemon/pma.py:create_token` → mints a scoped ephemeral MariaDB user + a
single-use signon token → returns `pma_url`) was never invoked from the UI
at all.

**Root cause, part 2 (same double-prefix bug, latent)**: `daemon/pma.py`'s
`create_token()` independently reimplemented the identical
`f"{username}_{suffix}"` scoping inline — would have hit the exact same
"database not found" failure the moment the frontend route were fixed to
pass the row's full `db_name`.

**Fix**:
- `pma.py:create_token` now calls the same `_resolve_existing_db_name`
  helper (imported from `handlers_database`) instead of reimplementing
  scoping — single source of truth, no more duplicated logic to drift.
- `Databases.jsx`: per-row "phpMyAdmin" menu action now `POST`s the real
  `.../pma-token` endpoint for that row's database and opens the returned
  `pma_url` in a new tab (guards the `pma_hostname`-unset case with a clear
  toast instead of silently doing nothing). Removed the page-header-level
  "phpMyAdmin" button, since phpMyAdmin sessions are scoped to exactly one
  database (no "default" database to open one for) — replaced with a hint
  pointing at the per-row action.

## Tests

`tests/test_handlers_database.py`: +3 (full-name accepted for drop/reset;
cross-account full-name lookup still 404s — the tolerant resolver must not
weaken the `account_id` ownership filter). `tests/test_pma.py`: +1
(full-name accepted by `create_token`). All existing bare-suffix tests
(legacy UI router, `db.create`) pass unchanged — confirms the fix is
additive/tolerant, not a contract break.

`source .venv/bin/activate && python3 -m pytest tests/test_handlers_database.py tests/test_pma.py -q`
→ 22 passed.

## Not done here

Live phpMyAdmin re-verification (the underlying `pma.py`/vhost/proxy
mechanism was already confirmed working infra-side by the investigation —
`phpmyadmin` apt package present, OLS vhost serving 200, DNS resolving; only
the UI wiring was broken) — deferred to the operator's own click-through
once this build is deployed, consistent with this project's established
"build+test now, live-verify after deploy" pattern for UI-only fixes.
