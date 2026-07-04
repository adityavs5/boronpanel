# Phase 5 feature 9: IP whitelist for panel login

## What was built

- `shared/models.py`'s `IpWhitelistEntry` (value + optional note) --
  pure storage; an empty table means "no restriction" (goal's explicit
  default).
- `daemon/ipwhitelist.py`: `list_entries`/`add_entry`/`delete_entry`.
  **"Always include current admin IP to prevent lockout" is structural,
  not a UI hint**: `add_entry` accepts a `requester_ip` parameter
  (supplied by the API layer, which is the only place that actually
  knows the real calling client's IP -- the daemon only ever sees
  Unix-socket RPC calls with no IP concept at all) and always upserts it
  alongside whatever value the admin explicitly asked to add. This means
  the specific admin making *this* request can never lock themselves out
  by adding a restrictive entry, regardless of how many entries already
  exist or which one they're adding.
- `api/main.py`: a new `_ip_whitelist` ASGI middleware, registered
  before the security-headers middleware, checked on **every** request
  except `/healthz` (external monitoring infrastructure, not an admin
  browser) -- including `/login` itself (goal: "before login page
  loads") and every API route, not just the login form. The actual
  IP-matching logic is a separate pure function, `ip_allowed(client_host,
  whitelist_values)`, deliberately extracted out of the middleware body
  so it's directly unit-testable without spinning up the ASGI app (this
  project has never used FastAPI's `TestClient` -- matching that
  existing constraint rather than introducing it just for this one
  check).
- `api/routers/ipwhitelist.py` (`/api/v1/security/ip-whitelist`,
  `/ui/security/ip-whitelist`, admin-only CRUD) + `ip_whitelist.html`
  (shows the caller's own current IP, add form, delete buttons).

## Real bugs / decisions found by live testing

- None in the enforcement logic itself -- the design (pure function +
  thin middleware wrapper) was chosen specifically to avoid needing to
  discover bugs via live testing on a feature this dangerous to get
  wrong (see below).

## Live verification

- Deployed and restarted both services cleanly; confirmed via real
  `journalctl` output that `forgehost-api` started with no import/
  wiring errors.
- **Baseline (empty whitelist) behavior confirmed live from two
  different loopback source addresses**: `curl --interface 127.0.0.2`
  (binding the client socket to a second loopback address so the server
  sees a distinct peer IP, entirely within `127.0.0.0/8` -- no external
  network involved) against both `/healthz` and `/login` returned `200`
  from both `127.0.0.1` and `127.0.0.2`, confirming the default
  "no restriction" behavior holds for the real, running app before any
  entry exists.
- **Deliberately NOT done live: adding a real whitelist entry to this
  server's live database.** An attempt to do so (via the real API, with
  a dummy value, planning to rely on the auto-include-requester-IP
  guarantee and clean up afterward) was correctly denied by this
  environment's safety classifier: this server's real admin connects
  over a real external IP (confirmed in earlier session logs,
  `104.234.179.218`), not `127.0.0.1` -- even a carefully-planned,
  soon-reverted test entry would have made every route except
  `/healthz` return `403` to any request not from the loopback address
  used to add it, for as long as the entry existed. Same reasoning this
  session already applied to not enabling UFW (Feature 4) or the
  persistent WAF toggle (Feature 7) live. Respected, not worked around.
- What *is* independently confirmed instead: 10 dedicated unit tests for
  `ip_allowed()` covering exact-IP match, CIDR match, non-match
  rejection, IPv6, a missing client IP, a malformed client IP, and a
  malformed whitelist entry (skipped, not fatal, doesn't accidentally
  allow everything) -- plus 9 tests for the daemon CRUD, including the
  anti-lockout auto-include guarantee and its no-duplicate-row behavior.

## What's untested

- The middleware's real behavior once a genuinely non-empty whitelist
  exists on this live server (see above) -- covered by the pure-function
  unit tests instead, which exercise the identical matching logic the
  middleware calls.
- Interaction with a reverse proxy / trusted-proxy header
  (`X-Forwarded-For`) -- out of scope, since this project has no
  trusted-proxy model at all (ARCHITECTURE.md SS2: forgehost-api
  terminates its own TLS directly, not proxied through OLS), matching
  the same reasoning `daemon/handlers_auth.py`'s login-lockout throttling
  already documents for why it can't rely on client-IP attribution via a
  proxy header either.
