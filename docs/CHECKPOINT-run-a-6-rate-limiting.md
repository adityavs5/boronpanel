# CHECKPOINT run-a-6 — API rate limiting

**Goal:** FastAPI middleware: login 10/5min/IP, password reset 5/hr/IP,
authed 300/min/user, unauthed 30/min/IP. Return 429 + Retry-After header.
In-memory sliding window, no Redis dependency. Log all hits.

## What was built

- **`api/ratelimit.py`**: exact sliding-window limiter (per-key deque of
  timestamps, pruned on every check) — not a fixed-window approximation,
  which would admit up to 2x the stated limit across a window boundary.
  Pure in-process/in-memory (goal's explicit no-Redis constraint);
  correct as a global view because forgehost-api runs as a single uvicorn
  process (deploy/forgehost-api.service has no --workers fan-out).
  Opportunistic stale-key sweep every 4096 checks bounds memory.
- **Tiers** (most-specific wins, one bucket charged per request):
  - `POST /login`, `POST /login/2fa` → 10 / 5 min / IP. Deliberately
    per-IP even when a valid session cookie is presented — a session must
    not buy extra brute-force attempts.
  - `POST /change-password` → 5 / 1 h / IP (this codebase's password
    "reset" surface; there is no unauthenticated forgot-password flow).
  - Authenticated → 300 / 1 min / credential, where "credential" is a
    SHA-256 fingerprint of the fh_session cookie value or Bearer token —
    no DB read on the hot path.
  - Unauthenticated → 30 / 1 min / IP.
  - Exempt: `/healthz` (external monitoring polls it; same reasoning as
    its IP-whitelist exemption) and `/static/` (one login-page load
    fetches a dozen hashed asset chunks; the HTML/API requests they hang
    off are still limited).
- **Two hardenings closing the manufacture-your-own-key bypass class**
  (found reviewing the initial design, both covered by tests):
  1. Only the panel's real credential carriers count as "authed": the
     `fh_session` cookie (parsed out of the Cookie header by name) or a
     `Bearer` Authorization header. Without this, `Cookie: junk=1` would
     buy the 300/min tier instead of 30/min.
  2. The authed tier also charges a **per-IP backstop bucket** at the
     same 300/min, checked first. Without it, rotating an (unverified —
     verifying would cost exactly the DB/crypto work this path avoids)
     credential value per request mints a fresh bucket every time and is
     never limited at all. Effective authed limit:
     min(300/min/credential across IPs, 300/min/IP across credentials).
- **Middleware** in `api/main.py`, registered between the security-headers
  middleware (outermost — 429s still get CSP/HSTS stamped) and the
  IP-whitelist middleware (innermost of the three — a rejected flood
  costs one in-memory check, never the whitelist's per-request DB read).
  429 body + `Retry-After` (integer seconds, rounded up per RFC 9110).
- **Logging**: every rejection logged at WARNING on `forgehost.ratelimit`
  with tier, bucket key, IP, path, retry-after. Currently lands in the
  uvicorn journal; feature 7 (request logging) routes API logs to
  /var/log/forgehost/ and will pick this logger up too.

## Tests

`tests/test_ratelimit.py` — 20 tests: window math (admit-to-limit,
slide-not-reset boundary, per-key independence, idle-key sweep),
classification (login/reset paths, authed vs unauthed, exemptions,
GET /login not counted as an attempt), credential detection (fh_session
among other cookies, junk cookies rejected, Basic auth ignored, Bearer
accepted, empty values), the goal's literal "11th login attempt returns
429" (and per-IP isolation), password-reset 5/hr, unauthed 30/min,
authed keyed per-credential across IPs, **rotation-flood capped by the
IP backstop**, junk-cookie lands in unauthed, exempt paths never
limited, rejection logging, and a real-ASGI-app middleware test
(TestClient against api.main on an isolated DB): 11th POST /login → 429
with integer Retry-After and security headers still stamped.

One pre-existing test bug fixed while getting the module green: the
slide-not-fixed boundary test double-freed a slot (`<=` cutoff prunes
the timestamp exactly at the boundary too).

## What's honestly still open / watch items

- The `/files` FileBrowser proxy counts against the authed 300/min like
  any other authenticated traffic. A very thumbnail-heavy directory
  listing could plausibly brush against it; if that shows up in practice
  the right fix is a considered exemption/higher tier for `/files/`, not
  a general limit raise. Kept to the goal's literal numbers for now.
- Single-process in-memory state resets on service restart (documented
  tradeoff of the no-Redis constraint; a restart forgives at most one
  window's worth of abuse).
