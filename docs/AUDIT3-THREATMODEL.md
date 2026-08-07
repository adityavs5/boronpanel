# Boron — Security Audit 3 Threat Model

Written **before** any Audit 3 fixes, per the audit goal's mandatory pre-work.
Companion to `docs/AUDIT3-FINDINGS.md`.

Scope: **all new attack surface added since Audit 2**
(`docs/AUDIT2-THREATMODEL.md` / `docs/AUDIT2-FINDINGS.md`, which covered
everything through the React SPA / Node-Python hosting / Redis / LSCache /
namespace isolation / cPanel import / notifications / webhooks / staging /
2FA). Since Audit 2 (2026-07-06), the following shipped:

- **File manager v2 — FileBrowser Quantum** (2026-07-09): a root-owned Go
  binary (`boron-filebrowser.service`, loopback-only) fronted by an
  authenticated reverse proxy in `boron-api` (`daemon/filebrowser.py`,
  `api/routers/filebrowser.py`).
- **Cloudflare Phase 2+3** (2026-07-09): multi-account token pool, proxy/
  real-IP rails, DNS-01 via Cloudflare, bulk migrate, UFW lockdown — backend
  landed, live gates blocked on an operator CF token (unchanged this audit;
  no live CF credential exists on this box, so §"in scope" below explains why
  it is out of this pass's active testing).
- **Run A — 9 features** (2026-07-10): **plan templates**
  (`daemon/plans.py`, `api/routers/plans.py`), **white-label branding upload**
  (`daemon/branding.py`, `api/routers/branding.py`), onboarding wizard,
  health monitoring, **rate limiting** (in-memory sliding window,
  `api/main.py`), request logging, API docs, installer.
- **Panel update system** (2026-07-10): version tracking, release pipeline,
  update check/one-click update/rollback/history
  (`daemon/updates.py`, `api/routers/update.py`) — deployed but dormant
  (`update_github_repo` unset).
- **Missing-features batch** (2026-07-11, uncommitted until this audit's
  pre-work snapshot): **IMAPSync** (`daemon/imapsync.py`,
  `api/routers/imapsync.py`), **maintenance mode**
  (`daemon/handlers_maintenance.py`, `api/routers/maintenance.py`),
  **wildcard domains** (`daemon/handlers_wildcard.py`,
  `api/routers/wildcard.py`), **custom error pages**
  (`daemon/custom_pages.py`, `api/routers/errorpages.py`), **per-mailbox
  spam filters** (`daemon/spamfilter.py`, `api/routers/spamfilter.py`),
  **site statistics** (`daemon/sitestats.py`, `daemon/geoip.py`,
  `api/routers/sitestats.py`), **DB monitor** (`daemon/dbmonitor.py`,
  `api/routers/dbmonitor.py`). Per `docs/CHECKPOINT-missing-features-batch.md`
  this batch has an unusual provenance (a "research only" subagent built it
  unauthorized) and already received one review+live-verification pass that
  found and fixed 9 bugs including 2 IDORs — Audit 3 re-verifies that pass
  independently rather than trusting its self-report.

This document does not re-derive the base trust model — it inherits Audit
1/2's actors/boundaries unchanged and states only what the new surface adds.

## 1. Inherited model (unchanged)

- **B3 — `boron-api` ↔ `borond` (Unix socket):** the daemon does
  **not** re-derive authorization; every RPC op the new surface adds is
  another place a missing `require_*_access` call in the API router is a
  silent, full cross-account compromise. This is again the single
  highest-likelihood bug class audited here (area 13).
- **B4 — one account ↔ another:** Linux DAC, docroot ACL, systemd
  `User=`/`Slice=`, Redis socket perms, OLS namespace gate — unchanged, not
  re-tested unless a new feature touches one of these mechanisms directly.

## 2. What the new surface changes about the attack model

### 2.1 New places customer/admin-controlled input reaches a privileged sink

| New sink | Reached via | Question this audit asks |
|---|---|---|
| `subprocess` argv to `imapsync` (root daemon) | migration `host`/`user`/`password` | Is it an argument list (never `shell=True`/string-built)? Is `password` kept out of argv (passfile) and out of any log line? |
| Outbound IMAP connection from the root daemon | migration source `host`/`port` | Internal/loopback/metadata SSRF — re-validated **in the daemon**, not just at the API layer, since the daemon is the one that actually dials out? |
| OLS vhost rewrite rules (`maintenance`, `wildcard`) | domain, bypass token | Can the 503 rewrite ever swallow `/.well-known/acme-challenge/`? Is the bypass token checked with a constant-time compare / rate-limited against guessing? |
| Filesystem path under `templates/error_pages/<domain>/` | error-page filename/content, custom `ErrorDocument` target | Path traversal outside the per-domain error-pages dir? Can `ErrorDocument` be pointed at an arbitrary server-absolute path? |
| Postfix `header_checks`/Sieve source (`spamfilter`) | blacklist/whitelist entry text | Does an entry get interpolated into a Postfix map or Sieve script without escaping (regex-metacharacter or Sieve-string injection)? |
| OLS access-log parser + MaxMind GeoIP DB (`sitestats`) | `User-Agent`/request line from arbitrary internet traffic; the GeoIP `.mmdb` download | Does a crafted `User-Agent`/referrer break the parser or get reflected unescaped into the admin dashboard? Is the MaxMind download integrity-checked (HTTPS + hash), not just fetched over plain HTTP? |
| `KILL <thread_id>` via a MariaDB admin credential (`dbmonitor`) | admin-supplied thread id from `SHOW PROCESSLIST` | Is the visible/killable process list scoped to the requesting admin's actual privilege, or can any authenticated user reach it? (admin-only by router design — re-confirm, not assume) |
| Reverse-proxied HTTP request into FileBrowser Quantum (root, loopback) | `X-Fb-User` header, request path | Does the proxy unconditionally strip any client-supplied `X-Fb-User` before injecting the trusted one? Cross-account path/source access through the proxy? |
| `plan.apply` writing account limit columns + reconciling cgroups/quota/Redis | admin-selected plan id, or a customer-reachable apply path if one exists | Is plan CRUD and apply admin-only, or can a customer self-apply/self-escalate limits via the API directly (not just via the UI)? |
| GitHub release tarball download + extraction (root, `update.rollback`/one-click update) | redirect chain, tarball member list | Does every redirect hop get re-validated against GitHub-owned hosts (already a documented deviation from "no redirects" — re-confirm the re-validation is real, not just claimed)? Are symlink/device/absolute-path members rejected before or only via `filter="data"`? Is there a window between SHA256 verification and extraction (TOCTOU) where the downloaded file could be swapped? Is the trigger endpoint admin-only server-side, not just UI-hidden? |
| `X-Forwarded-For` read by the rate limiter (`api/main.py`) | any unauthenticated client | Is the limiter keyed off a client-spoofable header with no trusted-proxy allowlist, letting a single attacker rotate the header value to bypass the login-attempt cap? |
| SVG file bytes served back as `image/svg+xml` (`branding`) | admin-uploaded logo/favicon | Is the `<script>`/`on*=`/`javascript:`/`foreignObject` rejection (Audit 2-era description in STATUS.md) actually complete against realistic obfuscation (mixed case, embedded null, XML entity tricks, `<use href>`, CDATA)? Filename path traversal on write? |

### 2.2 New data-at-rest / data-in-transit exposure

- **IMAPSync credentials** — a full source-mailbox password is submitted by
  the customer and must reach `imapsync`'s argv/env for one process
  invocation; the checkpoint claims a passfile approach and "no password
  column at all" on the job model. This audit independently re-confirms:
  DB schema, RPC params on the admin "active jobs" listing endpoint, and
  every log line the job writes.
- **Maintenance bypass token** — a shared secret that, if brute-forced or
  logged, lets an outsider view a supposedly-gated site. Rate limiting and
  log hygiene both matter here.
- **DNS-01 hook credentials for wildcard SSL** — same class as the existing
  PowerDNS/Cloudflare API-key handling; re-confirm the wildcard feature
  reuses the existing credential path rather than introducing a new one.
- **Update-system tarball** — downloaded and briefly staged before SHA256
  verification; if verification and extraction are not atomic against the
  same bytes, a TOCTOU substitution is a code-execution-as-root vector.

### 2.3 New DoS / abuse surface

- **IMAPSync concurrency** — an unbounded number of simultaneous migration
  jobs per account could exhaust the daemon's thread pool (same class as
  Audit 1 F7) or the account's own resources.
- **Maintenance-mode token guessing** — no rate limit would make the bypass
  token brute-forceable at network speed.
- **Site-stats log parsing** — parsing every request's `User-Agent`/referrer
  from public internet traffic is attacker-influenced input processed at
  scale; a pathological string (ReDoS-shaped) could be a CPU-DoS vector.

### 2.4 IDOR — the recurring class (area 13)

Every router listed in §"scope" above is new since the last full sweep
(Audit 2's table covered only the 10 routers that existed at the time).
`imapsync`/`maintenance`/`wildcard`/`errorpages`/`spamfilter`/`sitestats`/
`dbmonitor` additionally already have one documented self-found-and-fixed
IDOR pair (imapsync `get_status`/`cancel_migration`, spamfilter
`delete_entry` — see the checkpoint) from the batch's own review pass; this
audit re-verifies those fixes are actually present in the code (not just
described) and sweeps the same class across every other new route,
independent of that self-report.

## 3. Attacker goals this audit specifically tests (per area, matches the
## goal's 13 numbered items)

1. **IMAPSync** — inject a shell command via `host`/`user`/`password`; make
   the daemon dial an internal address as SSRF; view/cancel another
   account's migration job; find a password in a log line or process list;
   exhaust the daemon by queuing unlimited concurrent jobs.
2. **Maintenance mode** — brute-force the bypass token at network speed;
   enable maintenance on a domain belonging to a different account; block
   ACME HTTP-01 validation via the 503 rewrite; find the token in a log.
3. **Wildcard domains** — write a DNS record for a domain the caller doesn't
   own; create a wildcard that shadows/conflicts with an explicit subdomain
   in a way validation doesn't catch; expose a DNS-01 hook credential.
4. **Custom error pages** — traverse outside `error_pages/<domain>/`; set
   `ErrorDocument`/equivalent to an arbitrary server-absolute path; reach
   another account's domain's error-page config.
5. **Spam filters** — reach another mailbox's blacklist/whitelist entries by
   id (re-verify the documented fix); inject Postfix `header_checks`/Sieve
   syntax via an entry value; apply a filter reload without validating it
   first (same "validate before reload" invariant as ARCHITECTURE.md §7).
6. **Site statistics** — read another account's domain's log data; poison
   the GeoIP lookup or crash the parser via a crafted `User-Agent`; MITM the
   MaxMind DB download.
7. **DB monitor** — kill a query outside the invoking admin's intended scope
   (re-verify the documented CONNECTION_ADMIN-gap fix); leak
   `SHOW PROCESSLIST` output (which includes other accounts' query text) to
   a non-admin; reach the kill action as a customer.
8. **FileBrowser Quantum** — send a client-supplied `X-Fb-User` and see if
   the proxy honors it instead of stripping it (direct `curl` test); log in
   as account A and reach account B's files through the proxy; assess (not
   necessarily fix — architectural) the blast radius of the root FB process.
9. **Plan templates** — reach `/admin/plans` as a customer; call the
   apply-plan API directly as a customer against their own or another
   account; get a plan's limits to not actually take effect server-side
   (client-trusted limit).
10. **Panel update system** — redirect the update download to a
    non-GitHub host; smuggle a symlink member through tarball extraction;
    exploit a TOCTOU window between SHA256 verification and extraction;
    trigger an update/rollback as a non-admin via direct API call.
11. **Rate limiting** — spoof `X-Forwarded-For` to bypass the per-IP login
    lockout; find any authenticated or state-changing route that runs before
    the rate limiter / auth check.
12. **Branding upload** — get script execution via a crafted SVG (script
    tags, event handlers, `foreignObject`, less-obvious vectors); traverse
    the upload filename outside the branding asset dir; get the SVG served
    with a content-type that lets a browser execute it as HTML instead of an
    image.
13. **Full IDOR re-sweep** — for every router listed in the "scope" section
    above (13 routers/areas), confirm every route that accepts a resource
    identifier independently verifies the requesting identity owns that
    resource, before any daemon call or DB read/write.

## 4. Severity rubric (unchanged from Audit 1/2)

- **Critical** — root on the host, or cross-account data/credential
  compromise reachable by an authenticated customer.
- **High** — credential disclosure across a trust boundary, or a
  vertical/horizontal escalation with a meaningful precondition.
- **Medium** — defense-in-depth gap or DoS reachable by an authenticated
  actor; admin-gated exposure of a root-context action.
- **Low/Info** — hardening gap, accepted tradeoff, or requires an
  already-privileged precondition.

## 5. Explicitly out of scope

No new features, no style refactoring. Cloudflare Phase 2+3's live gates
remain blocked on an operator API token (unchanged since Audit 2 — code and
unit tests already exist; there is nothing new to audit live that wasn't
already covered structurally when it shipped, and it is not named in the
goal's 13 areas). Carry-forward deferred findings from Audit 1/2 (F14 CSRF
token, F16/A2-11 token expiry, A2-5 Redis socket squatting, A2-6 cPanel
import URL SSRF, A2-7 admin 2FA not mandatory) are not re-litigated unless
new code touches them directly.

## 6. Method

Same as Audit 1/2: read every new/changed module in full (not sampled),
cross-check every new router's ownership gating against the daemon-side
resource lookup, and independently re-verify (not trust) the missing-features
batch's own self-reported bug fixes by reading the actual current code at
the cited locations. Findings recorded in `docs/AUDIT3-FINDINGS.md` in the
goal's required format; Critical/High fixed immediately with regression
tests, Medium/Low documented and fixed only if trivial (<10min).
