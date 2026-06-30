# Checkpoint: Phase h — REST API + basic admin UI

## What was built

- **`daemon/handlers_auth.py`** — new forgehostd-side primitives:
  `panel_user.create`/`set_password`, `auth.create_session`/
  `revoke_session`, `auth.create_api_token`/`revoke_api_token`. Needed
  because forgehost-api only ever opens SQLite read-only (ARCHITECTURE.md
  §4) — even session/token bookkeeping, which isn't a privileged *system*
  action, still has to route through forgehostd since it's the only writer.
- **`shared/passwords.py`** — bcrypt directly, not via passlib (passlib's
  bcrypt backend is broken against bcrypt≥4.0, confirmed by hitting it
  directly — `AttributeError: module 'bcrypt' has no attribute '__about__'`).
- **`api/`** — the FastAPI app: `security.py` (session cookies via
  itsdangerous + a real revocable `Session` row, SHA-256-hashed bearer
  tokens, `require_admin`/`require_account_access`/`require_domain_access`),
  `rpc.py` (the only path to forgehostd), `main.py`, and one router per
  resource (`accounts`, `domains`, `dns`, `databases`, `mail`, `ssl_router`,
  `files`, `tokens`, `auth`) — each exposing both a JSON REST API under
  `/api/v1/...` and a server-rendered UI under `/ui/...`.
- **`api/templates_ui/`** + `static/forgehost.css` — plain server-rendered
  Jinja2 + HTML forms (POST-redirect-GET), no JS framework. ARCHITECTURE.md
  originally said "Jinja2 + htmx"; htmx was dropped during the build since
  every UI action turned out to be a plain form submit with a redirect —
  adding a vendored JS dependency for zero actual interactivity gain would
  have contradicted the project's own "fewer moving parts" instinct.
- `scripts/create_admin.py` — the only way to get a first login (no
  hardcoded default credentials, a direct response to RESEARCH.md §5's
  CyberPanel finding about insecure install defaults).
- 17 new unit tests for `api/security.py` (session/token identity
  resolution, RBAC boundary checks), 161 total passing.

## A real, structural deployment bug found immediately, before any
application-level testing could even begin

This repo had been developed at `/root/cpanel-clone` with `/opt/forgehost`
as a symlink into it (a pragmatic shortcut taken in Phase a, since dev box
== target box here). That works fine for `forgehostd` (root can traverse
anywhere), but **`/root` is mode 700** — the unprivileged `forgehost-api`
process can never traverse into `/root/cpanel-clone`, symlink or not. The
very first attempt to run anything as `forgehost-api` failed with a bare
`Permission denied` on the python interpreter itself.

**Fix**: `/opt/forgehost` is now a real, separate directory — `rsync`'d
from `/root/cpanel-clone` (the git checkout, source of truth) via the new
`scripts/deploy.sh`, with its own venv and world-readable permissions
(755/644 — there's nothing sensitive in the code tree itself; all secrets
live under `/etc/forgehost`, which keeps its own tight permissions). Every
code change in this phase (and any future one) needs `deploy.sh` run before
the running services pick it up — documented in README. `deploy.sh` itself
had a follow-up bug (see below).

## Other real bugs found by live testing (every one of these is something
unit tests alone would not have caught, since each is an artifact of real
multi-process/multi-user execution)

1. **`deploy.sh` broke its own venv.** The first version's blanket
   `chmod 644` pass wasn't scoped away from `.venv`, so every deploy reset
   `.venv/bin/uvicorn` (and everything else in `.venv/bin`) back to
   non-executable, producing `systemctl start forgehost-api` →
   `203/EXEC`. Caught on the very first service start attempt. Fixed by
   excluding `.venv` from the chmod pass, not just from `rsync`.
2. **`shared/db.py`'s read-only SQLite engine never actually parsed.**
   `create_engine(f"file:{path}?mode=ro", connect_args={"uri": True})`
   passes a raw sqlite3 URI where SQLAlchemy expects a SQLAlchemy URL —
   `ArgumentError: Could not parse SQLAlchemy URL`. This code path had
   existed since Phase a but was never actually exercised until
   forgehost-api's first real DB read, because every prior test ran
   `write_session()` as root. Fixed by relying on actual OS file
   permissions (the DB file is `0640 root:forgehost-api`) for read-only
   enforcement instead of a broken `mode=ro` URI trick — simpler, and was
   already the documented design. A second related bug in the same
   function (the WAL-mode pragma unconditionally tried to run on read-only
   connections too, which would itself require write access) was fixed in
   the same pass.
3. **The RPC socket's directory, not just the socket file, blocked
   forgehost-api.** `/run/forgehost` is created by systemd's
   `RuntimeDirectory=` as `root:root` (this service has no `Group=`
   override); the socket file itself was correctly `root:forgehost-api`,
   but `forgehost-api` could never traverse the directory to reach it —
   another bare, unhelpful `Permission denied`. Fixed by having
   `daemon/server.py` chown+chmod the socket's parent directory at
   startup too, not just the socket file.
4. **`call_daemon()`'s own `actor`/`role` parameters collided with
   op-specific params also named `role`.** Creating an API token (which
   itself has a `role` field — admin/customer) crashed with `TypeError:
   call_daemon() got multiple values for argument 'role'`. Fixed
   structurally, not by renaming around it: `call_daemon()` now takes the
   whole `Identity` object as its second argument instead of separate
   `actor`/`role` strings, so no op param name can ever collide with it
   again. A bulk `sed` rewrite across all routers missed two multi-line
   call sites (one in `accounts.py`, one in `dns.py`) and the
   pre-authentication login call in `auth.py` (which legitimately has no
   `Identity` yet, since the user is mid-login) — each found by actually
   exercising that specific code path live, not by code review.
5. **`dns.create_zone` attributed new zones to the admin's own panel
   login, not the hosting account.** The endpoint defaulted
   `username=identity.username`, which is correct for a customer (whose
   panel username happens to equal their hosting account username only by
   coincidence of test setup) but wrong for an admin — "admin" is not a
   hosting account, so the very first real attempt to create a zone as
   admin failed with `account 'admin' not found`. Fixed by deriving the
   owning account from the domain's existing `Domain` row (set by
   `domain.add`) instead of the caller's own identity.
6. **`ARCHITECTURE.md`'s own stated bind address was self-contradictory**
   — `127.0.0.1:9443` described as "reachable externally", which a
   loopback bind cannot be. Caught while actually wiring up the systemd
   unit and asking "how does the operator reach this." Corrected to
   `0.0.0.0:9443` (ARCHITECTURE.md updated in place, not silently).
7. **Secrets-file permission mismatch.** `api/security.py` needs
   `SESSION_SECRET` to verify cookies, but the existing `secrets.env` is
   correctly root-only (it also holds MariaDB/PowerDNS credentials
   forgehost-api has no business reading). Rather than loosen
   `secrets.env`'s permissions, split off a new `api-secrets.env`
   (`0640 root:forgehost-api`, `SESSION_SECRET` only) and made
   `shared/config.py` merge both, treating a `PermissionError` on either
   file as "no secrets from this source" instead of crashing.

## Real end-to-end verification performed

All of the following were exercised against the live system, through real
HTTP requests with `curl` (cookie jars for session auth, `Authorization:
Bearer` headers for token auth) — not mocked, not via the test client:

1. `scripts/create_admin.py` → real admin panel user → real login (`POST
   /login`) → real signed session cookie → real authenticated dashboard
   render listing every account this VM has ever had.
2. **Full account lifecycle through the web UI form**, not the RPC client
   directly: create account (`POST /ui/accounts`) → real Linux user
   confirmed via `id`; add domain (`POST /ui/accounts/{u}/domains`) → real
   OLS vhost confirmed on disk; create database (`POST
   /ui/accounts/{u}/databases`) → real MariaDB database confirmed via
   `SHOW DATABASES`; browse the file manager (`GET /ui/accounts/{u}/files`).
3. **Auth enforcement**: unauthenticated requests to both a UI page and a
   REST endpoint → `401`. A revoked API token → `401`. An invalid/garbage
   token → `401`.
4. **API token issuance and the billing-system integration path**: created
   a token via the UI, used it as a real `Authorization: Bearer` header
   against `/api/v1/accounts/{username}` and the admin-only
   `/api/v1/accounts` list — both succeeded; revoked it via the UI,
   confirmed the same token immediately stopped working.
5. **Customer RBAC boundary, the highest-stakes check in this phase**:
   created a customer panel user scoped to one account, logged in
   separately, confirmed: own account → `200`; another account (by
   username *or* by domain ownership) → `403`; admin-only actions
   (terminate, list-all-accounts) → `403` even against their own account
   where applicable.
6. **DNS zone ownership end-to-end**: admin created a zone for a
   customer's domain (exercising the bug-6 fix above), the customer could
   then view and edit records for *their* zone (`200`) but not another
   domain's zone (`403`); a record added through the UI form was confirmed
   live via `dig`.
7. **Mail through the customer's own session**: enabled mail for a domain,
   created a mailbox, confirmed with `doveadm auth test` that the mailbox
   is real and the password set through the web form actually works.
8. Cleaned up the test account (`account.terminate`) and confirmed the
   usual full teardown (Linux user, vhost, database, DNS zone, mail) still
   holds when triggered through the API layer instead of a direct RPC call.

## What's untested / explicitly deferred

- SSL issuance through the UI/API wasn't re-run with a real Let's Encrypt
  issuance in this phase (Phase f already did that thoroughly against the
  daemon directly) — only the routing/auth wiring was verified, not a
  second real certificate, to avoid unnecessary load against Let's
  Encrypt's production ACME service for a check that's really about HTTP
  plumbing, not certificate issuance.
- No automated test suite exercises the FastAPI app via `TestClient`/
  `httpx.AsyncClient` in-process — all Phase h verification was live HTTP
  against the running systemd service. This is real coverage (arguably
  stronger, since it caught the genuine multi-process bugs above that an
  in-process test client would have masked), but means there's no fast
  regression suite for the HTTP layer itself; `tests/test_api_security.py`
  covers the auth/RBAC logic in isolation instead.
- Rate limiting / brute-force protection on `/login` was not built (not in
  v1 scope; flagged here rather than silently absent).
- The lightweight database browser mentioned as a Phase d deferred item
  (in place of installing phpMyAdmin) was **not** built — Phase h ran out
  of remaining scope for it. The REST API (`/api/v1/accounts/{u}/databases`)
  and the database creation/deletion UI exist; browsing table contents
  inside a hosted database does not. Documented as a known v1 gap, not
  silently dropped.

## What to review first on wake-up

- The customer-RBAC boundary (item 5 above) is the single highest-stakes
  piece of code in this entire project — it's the only thing standing
  between hosting customers and each other's data over the public web
  interface. It was tested live and passed, but deserves an independent
  read of `api/security.py`'s three `require_*` functions before trusting
  it with real customer accounts.
- `scripts/deploy.sh` is now a required step after every code change to
  this repo — anyone continuing this work needs to know `/opt/forgehost`
  is a deployed copy, not the live-edited source.
