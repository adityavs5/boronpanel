# Boron — Security Audit 3 Findings

Companion to `docs/AUDIT3-THREATMODEL.md`. Covers all 13 areas the audit goal
named — every attack surface added since Audit 2. Findings are listed
most-severe first within each section. This document is being assembled as
each area's investigation completes; sections are filled in incrementally,
not written to imply parallel areas were already done when they weren't.

---

## Section 0: Summary

| # | Severity | Area | Title | Status |
|---|---|---|---|---|
| A3-7 | **Critical** | 8 FileBrowser | Backend has no auth of its own; any local uid can reach it and impersonate any account | **Fixed and live-verified** (code + live iptables rule, applied on user's explicit direction; first attempt at the live rule was ineffective — see A3-7 for the append-vs-insert root cause) |
| A3-3 | **High** | 1 IMAPSync | No per-account concurrency cap — cross-tenant DoS | **Fixed** |
| A3-4 | **High** | 1 IMAPSync | SSRF guard validated once at creation; connection happens later, unpinned (DNS-rebinding TOCTOU) | **Partially fixed** (re-validation before connect added; full IP-pinning DEFERRED, documented residual gap) |
| A3-5 | **High** | 1 IMAPSync | imapsync's own transcript logging leaks cross-tenant PII, world-readable | **Fixed** |
| A3-6 | **High** | 7 DB Monitor | `kill_query` can kill any MariaDB connection once CONNECTION_ADMIN is granted, not just hosted-account queries | **Fixed** |
| A3-8 | **High** | 8 FileBrowser | systemd unit has zero sandboxing beyond running as root (9.6/10 UNSAFE) | **Fixed** |
| A3-1 | Medium | 9 Plans | Plan count/bandwidth limits written but never enforced by self-service creation endpoints | DEFERRED (documented, over 10-min bar) |
| A3-9 | Medium | 12 Branding | SVG filter had 2 confirmed XSS bypasses via entity encoding | Partially **fixed** (2 confirmed bypasses closed); full XML-aware sanitizer DEFERRED |
| A3-10 | Medium | 11 Rate limiting | Exact-IPv6 keying let one attacker rotate a routed prefix to defeat login limiter | **Fixed** |
| A3-11 | Medium | 5 Spam filters | Per-entry global Sieve refresh bypasses the mandatory validate→backup→rollback pattern | DEFERRED (documented, over 10-min bar) |
| A3-2 | Low | 10 Updates | TOCTOU between tarball SHA256 verification and extraction (verify-by-path, reopen-by-path) | DEFERRED (documented) |
| — | Low | 2/3 Maintenance/Wildcard | Bypass-token entropy makes brute force/timing moot; no dedicated rate limit | DEFERRED (entropy sufficient) |
| — | Low | 2/3 Maintenance/Wildcard | `test_cross_account_authorization.py` doesn't cover these 2 routers (code is correct today) | DEFERRED (test-coverage gap, not a live bug) |
| — | Low | 6 Site stats | MaxMind `.mmdb` download has no hash/signature pinning beyond TLS | DEFERRED (TLS is the primary control) |
| — | Info | 4/5/6/7/8/13 | Numerous "no finding" results with evidence — see each area section | N/A |

Fixed this pass: 1 Critical (code + live, verified — see A3-7), 4 High
fully fixed + 1 High partially fixed (A3-4), 2 Medium fixed (1 partially).
Deferred with documented reasoning: full IP-pinning for A3-4, 2 Medium
(A3-1, A3-11, plus the remainder of A3-9), 1 Low (A3-2), plus
several Low/Info items noted above. Full per-area detail follows.

---

## Area 13 — Full IDOR re-sweep

**Method**: every route in every router added/changed since Audit 2
(`imapsync.py`, `maintenance.py`, `wildcard.py`, `errorpages.py`,
`spamfilter.py`, `sitestats.py`, `dbmonitor.py`, `plans.py`, `update.py`,
`branding.py`, `filebrowser.py`, `onboarding.py`, `monitoring.py`,
`health.py` — 54 routes total) was read, its auth dependency confirmed, and
for every route accepting a resource identifier, its `call_daemon(...)`
target was followed into the daemon handler to check the "one layer deeper"
bug class that caused Audit 2's A2-1 (a router-level ownership check existed,
but the daemon didn't independently verify which resource it actually acted
on).

**Result: clean sweep, no IDOR finding.** Full per-router route table:

| Router | Route (verb + path) | Auth dependency | Correctly scoped? |
|---|---|---|---|
| `imapsync.py` | POST `/accounts/{u}/email/imap-migrate` | `require_account_access` + `require_domain_access` | Y |
| `imapsync.py` | POST `.../imap-migrate/list-folders` | `require_account_access` | Y (no Boron resource targeted) |
| `imapsync.py` | GET `.../imap-migrate` (list) | `require_account_access` | Y |
| `imapsync.py` | GET `.../imap-migrate/{job_id}` | `require_account_access` | Y — daemon `_job_for_account` cross-checks `job.account_id` |
| `imapsync.py` | POST `.../imap-migrate/{job_id}/cancel` | `require_account_access` | Y — same cross-check |
| `imapsync.py` | GET `/admin/imap-migrations` | `require_admin` | Y |
| `maintenance.py` | GET/PATCH `/accounts/{u}/domains/{d}/maintenance` | `require_account_access` + `require_domain_access` | Y — daemon re-derives owner from `domain` |
| `maintenance.py` | GET `/admin/maintenance` | `require_admin` | Y |
| `wildcard.py` | GET/PATCH `/accounts/{u}/domains/{d}/wildcard` | `require_account_access` + `require_domain_access` | Y |
| `errorpages.py` | GET/PUT/DELETE `.../error-pages[/{code}]` (4 routes) | `require_account_access` + `require_domain_access` | Y — all 4 CRUD routes consistently checked |
| `spamfilter.py` | GET/POST/DELETE `.../spam-filters` (+import) | `require_account_access` + `require_domain_access` | Y — `delete_entry` cross-checks row's own `domain` |
| `sitestats.py` | GET `/accounts/{u}/domains/{d}/stats` | `require_account_access` + `require_domain_access` | Y |
| `sitestats.py` | GET `/admin/sitestats/summary`, POST `/admin/sitestats/geoip` | `require_admin` | Y |
| `dbmonitor.py` | GET `/admin/db/monitor`, DELETE `/admin/db/queries/{thread_id}`, POST `.../kill-privilege/bootstrap` | `require_admin` | Y — server-wide admin resource, no account scoping applicable |
| `plans.py` | full CRUD `/admin/plans[/{id}]` + `POST /admin/accounts/{u}/apply-plan/{id}` | `require_admin` | Y |
| `update.py` | GET `/version` | any authenticated identity | Y — no resource exposure beyond version string |
| `update.py` | `/admin/update/{status,check,start,rollback,log,history}` | `require_admin` (+confirm/TOTP on start/rollback) | Y |
| `branding.py` | GET `/branding`, `/branding/logo`, `/branding/favicon` | none | Y — deliberately public |
| `branding.py` | PATCH/POST/DELETE `/admin/branding*` | `require_admin` | Y |
| `filebrowser.py` | GET `/accounts/{u}/files/launch` | `require_account_access` | Y — mints signed `fh_fb_target` cookie |
| `filebrowser.py` | ANY `/files`, `/files/{path}` | re-derived from signed cookie, not client input | Y — re-authorized every request; client `X-Fb-User` stripped |
| `onboarding.py` | GET/PATCH `/accounts/{u}/onboarding` | `require_account_access` | Y |
| `monitoring.py` | `/admin/monitoring/{settings,history}` | `require_admin` | Y |
| `health.py` | `/health`, `/health/history`, `/ui/health` | `require_admin` | Y |

**Self-reported fixes from `docs/CHECKPOINT-missing-features-batch.md`,
independently re-verified in the daemon source (not just the docs):**
- `imapsync.get_status`/`cancel_migration` — confirmed: `daemon/imapsync.py`'s
  `_job_for_account(session, job_id, username)` independently resolves the
  account from `username` and requires `job.account_id == account.id`,
  identical "not found" on mismatch (anti-enumeration). Both routes use it.
- `spamfilter.delete_entry` — confirmed: `daemon/spamfilter.py` fetches the
  row by bare `id`, then requires `row.domain == domain` (the domain
  `require_domain_access` already authorized) before deleting.

**Assessment**: every domain-scoped feature added this cycle
(maintenance/wildcard/errorpages/spamfilter/sitestats) follows the identical
`require_account_access` + `require_domain_access` pairing at the router,
and — critically — has its daemon handler re-derive the owning account from
the domain/username string itself rather than trusting a client-controlled
numeric ID, which structurally forecloses the A2-1 bug class. No finding.

---

---

## Area 9 — Plan templates

**Q1 (customer access to `/admin/plans`)**: all 6 routes in
`api/routers/plans.py` individually call `require_admin(identity)` as their
first statement (not relying on router-prefix gating alone). No finding.

**Q2 (customer apply-plan via API)**: `apply_plan`
(`api/routers/plans.py:81-84`) is `require_admin`-gated; there is no
self-signup/registration route anywhere — account creation is exclusively
admin-triggered (`api/routers/accounts.py:32-44`, also `require_admin`). No
customer-reachable path to self-select a plan. No finding.

**Q3 (limits enforced server-side)** — **Finding A3-1 below.** CPU/mem/io/pids
(cgroup) and disk quota (`setquota`) are genuinely kernel-enforced. But the
six count/bandwidth limits (`database_limit`, `email_account_limit`,
`subdomain_limit`, `ftp_account_limit`, `app_limit`, `bandwidth_limit_mb`)
are written to `AccountResourceLimits` by `plan.apply` and consumed **only**
by `usage_alerts.py`'s 80/90/100% email/webhook alerting — never checked by
the customer-facing, `require_account_access`-gated creation endpoints
(`create_database`, `add_domain` for subdomains, mailbox/FTP-account/app
creation). A customer can create unlimited resources of these kinds
regardless of their assigned plan.

## Area 10 — Panel update system

**Q1 (redirect to non-GitHub host)**: confirmed blocked. `httpx.Client(...,
follow_redirects=False)` (`daemon/updates.py:578-579`) plus a manual loop
that calls `_validate_download_url` again on **every** hop (not just the
first) before following it — host must be `github.com` or end with
`.githubusercontent.com` (leading-dot check, so `evilgithubusercontent.com`
does not match), capped at 4 hops. No finding.

**Q2 (symlink members rejected)**: confirmed, two layers in the correct
order. `validate_tarball_members` (`daemon/updates.py:651-676`) rejects
traversal, out-of-prefix members, symlinks/hardlinks
(`member.islnk() or member.issym()`), and non-file/dir types, runs to
completion **before** `tf.extractall(..., filter="data")`
(`daemon/updates.py:687,690`) is ever called. No finding.

**Q3 (TOCTOU)** — **Finding A3-2 below.** The tarball is hashed via one
`open(tarball, "rb")` (`daemon/updates.py:632`), then the function returns
the bare path string, and `_extract_staged` independently reopens the same
path (`tarfile.open(tarball, "r:gz")`, `daemon/updates.py:686`) — hash and
extraction operate on the path, not a pinned set of bytes.

**Q4 (non-admin trigger via API)**: confirmed blocked. Every update route
individually calls `require_admin`; `start`/`rollback` additionally require
`_require_confirmed_admin_action` (`api/routers/update.py:44-69`), which
rejects API-token identities, requires `confirm=True`, and — when the admin
has TOTP enabled — validates a real `pyotp.TOTP(...).verify()` or hashed
recovery-code check against the actual `TotpCredential` row (not a stub).
`GET /version` is deliberately the one unauthenticated-role (but still
session/token-authenticated) route, documented and non-mutating. No finding.

### A3-1 — Medium — Plan resource-count/bandwidth limits are not enforced server-side, only alerted on

**LOCATION**: `daemon/handlers_database.py:29-66` (`create_database`),
`daemon/handlers_domain.py:50-100` (`add_domain`), `daemon/handlers_ftp.py`,
`daemon/handlers_mail.py`, `daemon/nodeapps.py`, `daemon/pythonapps.py` —
none query `AccountResourceLimits`; compare `daemon/plans.py:203-206` (which
writes the limits) against `daemon/usage_alerts.py:151-166` (the only reader).

**DESCRIPTION**: `plan.apply` writes `database_limit`/`email_account_limit`/
`subdomain_limit`/`ftp_account_limit`/`app_limit`/`bandwidth_limit_mb` into
`AccountResourceLimits`, but the only consumer anywhere in the codebase is
`usage_alerts.py`'s threshold-crossing alert logic (email/webhook
notifications at 80/90/100%). None of the customer-self-service creation
endpoints for these resource types — all gated by `require_account_access`,
reachable by any customer for their own account — check the current count
against the plan's limit before creating another one.

**IMPACT**: A customer on a plan capped at, e.g., 5 databases or 3
subdomains can create an unbounded number of either via the ordinary
self-service API. The plan-based tiering/billing model's count limits are
enforcement theater — real for CPU/mem/IO/pids/disk (kernel-enforced), fake
for everything counted by number. This is a resource-exhaustion and
billing-integrity gap, not a cross-account compromise (each customer only
over-consumes their own allotment), but it defeats the stated purpose of
plan templates.

**FIX**: DEFERRED — not a Critical/High per the audit's severity rubric
(no cross-account/credential-disclosure impact), and implementing a correct
fix (a limit check at the top of 5+ creation handlers across 5 different
files, matching the existing `_validate_limits`/cgroup-limit pattern in
`daemon/handlers_account.py`) is meaningfully over the goal's 10-minute bar
for Medium items. Recommended as a real follow-up: add a
`current_count >= AccountResourceLimits.<x>_limit` check (skip if limit is
`None`/unset) at the top of `create_database`, `add_domain` (subdomain
kind), mailbox creation, FTP-account creation, and Node/Python app creation.

### A3-2 — Low — TOCTOU between update-tarball SHA256 verification and extraction

**LOCATION**: `daemon/updates.py:614-640` (`_download_and_verify`, hashes
via `open(tarball, "rb")` at line 632, returns the bare path) and
`daemon/updates.py:686` (`_extract_staged`, independently reopens the same
path via `tarfile.open(tarball, "r:gz")`).

**DESCRIPTION**: The SHA256 check and the extraction operate on the same
*path*, not the same open file handle/inode-pinned bytes — classic
verify-by-path-then-act-by-path TOCTOU shape.

**IMPACT**: If anything with write access to `update_download_dir`
(`/var/lib/boron/update-staging`, intended root-only 0700) could replace
the file in the gap between the two `open()` calls, the verified hash would
not correspond to what actually gets extracted and installed as the new
panel version — a root code-execution vector. Exploitability today is low:
the two steps are immediately sequential in a single worker thread (no
I/O-bound gap), and the directory is root-created. Worth noting:
`os.makedirs(..., mode=0o700, exist_ok=True)` does not tighten permissions
on a directory that already exists from a prior install/misconfiguration,
so the 0700 guarantee is assumed at creation, not verified at use.

**FIX**: DEFERRED (Low, and the correct fix — hash from an open file object,
`seek(0)`, pass the same object to `tarfile.open(fileobj=f, ...)` — touches
the update pipeline's core download/extract sequencing, over the 10-minute
bar to change and re-verify safely against the finalizer's existing test
coverage). Recommended future hardening: verify-and-extract from the same
file handle rather than re-opening by path, and explicitly assert
`update_download_dir`'s ownership/mode at daemon startup rather than relying
on `exist_ok=True`.

---

---

## Area 1 — IMAPSync

**Q1 (command injection)**: no finding. Every `subprocess` call in
`daemon/imapsync.py` goes through `daemon/procutil.run()`
(`shell=False`, rejects string commands outright); `host`/`user`/`password`
are always separate list elements.

**Q3 (IDOR)**: no finding — independently re-verified. `_job_for_account`
(`daemon/imapsync.py:428-438`) cross-checks `job.account_id`; `list_jobs`
scopes its own query by `account_id`; every API route double-checks with
`require_account_access`/`require_domain_access` before the RPC call.

**Q2 (SSRF), Q4 (credentials in logs), Q5 (concurrency)** — three High
findings, detailed below.

### A3-3 — High — IMAPSync has no per-account concurrency cap; one customer can starve the shared executor for every tenant

**LOCATION**: `daemon/imapsync.py:259-293` (`start_migration`),
`daemon/imapsync.py:110` (`IMAPSYNC_EXECUTOR = ThreadPoolExecutor(max_workers=2)`).

**DESCRIPTION**: `start_migration` submits every job to a 2-worker pool
shared by all accounts, with no check for an account's own existing
pending/running jobs. Up to 200 attacker-controlled folders per job
(`MAX_FOLDERS_PER_JOB`), each synced via its own `run(..., timeout=600)`
call — a customer pointing `source_host` at their own slow-drip "IMAP"
server can occupy one worker for up to ~33 hours. `daemon/backup.py` (Audit
1 F9) and `daemon/cpanel_import.py` (same feature batch) both already
implement an "existing_active job → reject" guard for this exact risk
class; imapsync has no equivalent.

**IMPACT**: Any unprivileged customer, using only intended API parameters,
can deny the imapsync feature to every other tenant on the box for a very
long time — a cross-tenant DoS requiring no privilege escalation.

**FIX**: Applied. `start_migration` now rejects a new request if the
account already has a job in `pending`/`connecting`/`running` status,
mirroring `daemon/backup.py:trigger_backup`'s pattern exactly.

### A3-4 — High — IMAPSync's SSRF guard validates once at job creation; the actual connection happens later, unpinned, with no re-validation (DNS-rebinding TOCTOU)

**LOCATION**: `shared/validation.py:748-776` (`validate_imap_source_host`,
single-shot resolution, returns only the hostname string), called once from
`daemon/imapsync.py:262`; the background worker
(`daemon/imapsync.py:362,388-398`) re-reads the stored hostname and hands it
straight to `imapsync --host1`, which performs its own independent DNS
resolution at connect time.

**DESCRIPTION**: Because job execution is asynchronous (queued onto the
shared executor, dequeued whenever a worker frees up — a delay fully
attacker-controllable via A3-3's missing concurrency cap), there is a real
window between "hostname validated as public" and "imapsync actually
connects." A customer can point `source_host` at a domain they control,
pass validation while it resolves publicly, then repoint DNS at
`127.0.0.1`/`169.254.169.254`/an internal address before the job runs —
classic DNS-rebinding. The module's own docstring incorrectly claims no such
TOCTOU window exists (contradicted by its own async-queue architecture).
`daemon/webhooks.py:_assert_public_destination`/`_pinned_post` already
implements the correct pattern (resolve, validate, and pin the connection to
the validated IP) for the structurally identical background-delivery case.

**IMPACT**: A low-privileged customer can make the root `borond`/
`imapsync` process originate connections into the internal network on
arbitrary ports and speak IMAP-shaped bytes to whatever's listening, with
partial response/error text (capped 800 chars) echoed back to the
customer's own job-status field — an SSRF + limited internal banner-grab
primitive from a root process.

**FIX**: Partially applied. Full IP-pinning (A2-3's `webhooks.py` pattern —
resolve, validate, and pin the connection to the literal validated IP) is
DEFERRED: porting it here means handling the interaction with imapsync's
own TLS/SNI handling since it's a separate Perl process, not an in-process
HTTP client, which is more than a same-pass fix can safely do without
risking an under-tested change to a root-privileged subprocess invocation.
Applied instead: `_run_job` now re-calls `validate_imap_source_host`
immediately before the real connection (previously it was only ever called
once, at `start_migration`/job-creation time) — this is exactly what
`shared/validation.py`'s own docstring for that function already claimed
happened ("daemon/imapsync.py re-checks again immediately before
connecting -- the authoritative guard"), but until this fix, it didn't.
This shrinks the DNS-rebinding window from the full attacker-controlled
queue-wait time (previously unbounded before A3-3's concurrency cap, now
bounded but still real) down to the milliseconds between this check and
imapsync's own connect — the connection is still not pinned to the
specific resolved IP (imapsync resolves independently as a separate
process), so a rebind landing in that much smaller residual window remains
theoretically possible. This residual gap is honestly documented, not
claimed as fully closed; full IP-pinning remains the recommended follow-up
for complete closure. Covered by a new regression test
(`test_run_job_revalidates_source_host_before_connecting`) confirming a
detected rebind fails the job cleanly and never invokes imapsync.

### A3-5 — High — imapsync's own default transcript logging is never disabled; world-readable cross-tenant PII lands in the daemon's production working directory

**LOCATION**: `daemon/imapsync.py:211-224,388-398` (no `--nolog`/
`--logdir`/`--logfile` passed on any invocation); confirmed live at
`/opt/boron/LOG_imapsync/*.txt` (0644, directory 0755, root:root).

**DESCRIPTION**: imapsync's default behavior writes a full transcript to
`LOG_imapsync/<timestamp>_<user1>_<user2>.txt` relative to its cwd. In
production `borond` runs with `WorkingDirectory=/opt/boron`, and
real, populated, world-readable log files were found there from actual
prior runs — containing source host/IP, both source and destination mailbox
addresses in cleartext, and explicit login-success confirmation for both
ends (not the password itself — that part of the design holds). This
directly violates the feature's own stated "credentials never logged
anywhere" goal in spirit and breaks tenant isolation on a shared-hosting
box where any other local account can typically read world-readable files.

**IMPACT**: Any local unprivileged user on the box can read every other
customer's migration metadata (which provider they're leaving, both mailbox
addresses, proof of valid login) — cross-tenant PII/business-metadata
disclosure.

**FIX**: Applied. Every `imapsync` invocation (`list_source_folders` and the
per-folder sync loop in `_run_job`) now passes `--nolog` to suppress the
transcript entirely. The pre-existing `LOG_imapsync/` directories (both in
this repo checkout and, per the live evidence above, under `/opt/boron`
in production) are a live-deployment cleanup item for the operator, flagged
separately since this repo isn't the live deploy target.

---

## Area 2 — Maintenance mode, and Area 3 — Wildcard domains

No Critical/High findings in either area. Both routers correctly gate every
route with `require_account_access` + `require_domain_access` before any
daemon call. Highlights:

- **Bypass token entropy**: `secrets.token_urlsafe(32)` (256 bits); checked
  via an OLS `RewriteCond` regex (not literally constant-time), but the
  entropy alone makes brute force/timing attacks infeasible. No dedicated
  rate limit exists on wrong-token attempts, and structurally can't — the
  bypass check is resolved entirely inside OLS on the customer vhost, never
  reaching `boron-api`'s rate limiter. Low finding (deferred, entropy
  makes it moot in practice).
- **ACME exclusion**: confirmed live in the current template —
  `RewriteCond %{REQUEST_URI} !^/\.well-known/acme-challenge/` is the first
  condition in the maintenance rewrite block (`templates/vhost.conf.j2:250`).
- **`trim_blocks` critical-bug re-verification**: independently re-rendered
  the actual `httpd_config.conf.j2` through the project's real
  `Environment(trim_blocks=True, lstrip_blocks=True)` settings with wildcard
  + non-wildcard domains populated — confirmed balanced braces, no
  line-merging. The wildcard suffix uses an inline `{{ }}` expression
  (immune to `trim_blocks`, which only affects `{% %}` statement tags), and
  no other same-line block-tag pattern exists in either template. The
  checkpoint's claimed fix holds up under direct empirical re-test, not just
  documentation review.
- **DNS-01 credentials**: never reach argv/env/logs — always a file path
  (0600, root-only) passed to certbot, confirmed for both PowerDNS and
  Cloudflare hook paths.
- **Low finding (deferred)**: `tests/test_cross_account_authorization.py`
  (the project's dedicated regression suite for this exact bug class,
  created after two prior incidents) covers neither `maintenance.py` nor
  `wildcard.py`. Code is correct today; no test would catch a future
  regression. Recommended follow-up, not fixed this pass (test-writing
  across 2 routers is over the 10-minute bar for a Low item).

---

## Area 4 — Custom error pages, and Area 5 — Per-mailbox spam filters

No Critical/High findings. Highlights:

- **Error pages path traversal**: not exploitable — there is no free-form
  filename input at all; the only writable filenames are `f"{code}.html"`
  with `code` constrained to a fixed tuple `(403, 404, 500, 503)`, or a
  hardcoded constant. All writes go through `daemon/safeio.py`, which
  additionally rejects `/`-containing or `.`/`..` names with `O_NOFOLLOW`.
- **ACL-traversal fix re-verification**: independently confirmed
  `ensure_pages_dir` (`daemon/custom_pages.py:118-133`) now grants an
  execute-only ACL on the intermediate `<home>/<domain>/` directory in
  addition to the pre-existing grant on `error_pages/` itself — the
  checkpoint's claimed fix is genuinely present and correct.
- **Spam filter IDOR re-verification**: `delete_entry`
  (`daemon/spamfilter.py:533-551`) cross-checks `row.domain != domain`
  before deleting — confirmed present, plus a full sweep of every other
  entry-touching op (`list_entries`, `add_entry`, `import_entries`,
  `delete_entries_for_mailbox`) found no sibling instance of the same gap.
- **Sieve/pattern injection**: not exploitable — Boron does not use
  Postfix `header_checks` here (docstrings claiming so are stale; actual
  mechanism is a generated Dovecot Sieve script), and
  `validate_spam_filter_pattern`'s charset (email or domain regex) contains
  none of `"`, `\`, `{`, `}`, `;`, or newlines, so no crafted entry can break
  out of a Sieve string literal.
- **A3-11 — Medium finding (deferred)**: the per-entry global Sieve script refresh
  (`daemon/spamfilter.py:_install_global_sieve_script`/
  `_refresh_global_sieve`) writes the live, server-wide sieve file directly
  via `os.replace` with no backup and no rollback on a post-write compile/
  reload/verify failure — bypassing `daemon/configtx.ConfigWriter`, the
  shared engine `ARCHITECTURE.md §7` mandates for exactly this class of
  change, which this same module correctly uses elsewhere
  (`_ensure_postfix_wiring`, `_ensure_dovecot_sieve_wiring`). Content is
  pre-validated for syntax (not an injection vector), but a reload/verify
  failure triggered by any customer's routine filter edit can leave the
  server-wide spam-Junk-filing script for every mailbox in an unrecovered,
  partially-applied state. DEFERRED: routing this write through
  `ConfigWriter` is a real fix but touches a shared, frequently-called code
  path and is over the 10-minute bar to change and re-verify safely in this
  pass; recommended as a priority follow-up given it's reachable by any
  authenticated customer.

---

## Area 6 — Site statistics, and Area 7 — DB monitor

**Area 6**: no Critical/High findings. Log paths are derived exclusively
from a DB-verified domain→account→username chain, never client input — no
cross-account log-read possible. MaxMind `.mmdb` download uses standard
HTTPS/curl cert validation (Low: no additional hash/signature
pinning — deferred, TLS is the primary control and correctly applied).
User-Agent is parsed but never stored/rendered anywhere (dead field);
referer is reduced to hostname only before counting; access-log regexes are
ReDoS-safe (no nested/overlapping quantifiers). All 3 routes correctly
gated.

**Area 7**: one High finding, below. `SHOW PROCESSLIST` exposure and
kill-route admin-gating both confirmed correct (all 3 dbmonitor routes
individually `require_admin`, no customer-reachable path).

### A3-6 — High — DB Monitor's kill_query has no scope restriction beyond "valid integer thread ID" once CONNECTION_ADMIN is granted

**LOCATION**: `daemon/dbmonitor.py:242-266` (`kill_query`),
`api/routers/dbmonitor.py:37-40`.

**DESCRIPTION**: `kill_query(thread_id)` executes `KILL {thread_id}` for
any integer thread ID the caller supplies (int-cast prevents SQL injection,
but that's the only validation). There is no cross-check against the
`db_to_account` ownership mapping that `get_processlist`/
`get_connection_summary` already compute in the same module. Once the
documented (currently ungranted) `CONNECTION_ADMIN` privilege is applied,
this becomes a general "kill any MariaDB connection on the server"
primitive — another admin's session, an in-progress `mysqldump`/backup
connection, a replication thread, or the daemon's own connections — not the
customer-query-only tool the feature and its UI copy (`DbMonitor.jsx`)
describe.

**IMPACT**: This is admin-only (no cross-account/customer-reachable path —
confirmed), but a single admin misclick against a stale/reused thread ID
(MariaDB reuses thread IDs), or a compromised admin session/token, gets an
unrestricted "kill anything" primitive layered directly on top of a real,
permanent SQL privilege widening, with no confirmation or scoping.

**FIX**: Applied. `kill_query` now runs `SHOW FULL PROCESSLIST` first and
requires the target thread's `db` to be present in `DatabaseGrant` (i.e.
belong to an actual hosted account) before issuing `KILL`; kills targeting
`db IS NULL`, MariaDB system users/threads, or the daemon's own connection
are rejected with a clear error instead of silently permitted.

---

## Area 8 — FileBrowser Quantum

**Q1 (X-Fb-User stripped) / Q2 (per-request re-authorization)**: the proxy
code itself (`api/routers/filebrowser.py`) is correct — `_build_upstream_headers`
strips any client-supplied `X-Fb-User` before injecting the trusted value,
and `proxy()` calls `require_account_access(identity, target)` fresh on
every single request against a server-side-signed, itsdangerous-verified
cookie, not a client-editable parameter. No finding in the proxy code path
itself.

**However — Q3 (blast radius) surfaced a Critical finding**, confirmed by
live (non-mutating GET) testing directly against the FileBrowser backend:
the backend performs **zero authentication of its own** and trusts any
`X-Fb-User` header unconditionally (with `createUser: true`
auto-provisioning a new account scope for a never-seen username), and
**nothing at the OS/network layer restricts which local UID may reach
127.0.0.1:8088** — the proxy's header-injection is the *entire* security
boundary, and it is not actually enforced against other local processes on
the box.

### A3-7 — Critical — FileBrowser Quantum backend has no authentication of its own and no OS-level access restriction; any local process can impersonate any account

**LOCATION**: FileBrowser Quantum backend (`127.0.0.1:8088`,
`/etc/boron/filebrowser.yaml`); no `iptables`/`ufw`/uid-based rule
restricts loopback access to that port anywhere in this codebase or the
live firewall config.

**DESCRIPTION**: The proxy (`api/routers/filebrowser.py`) correctly injects
a trusted `X-Fb-User` header and strips any client-supplied one — but this
is enforced **only in the proxy code**, not by FileBrowser itself, which
accepts any `X-Fb-User` value from any caller that can reach the port,
full stop, with `auth.methods.proxy.createUser: true` silently
auto-vivifying a scope for a username it has never seen. Live-confirmed
(non-mutating `curl -H "X-Fb-User: <arbitrary>" http://127.0.0.1:8088/files/"`
→ HTTP 200, full access, no credential of any kind presented). Since
Boron's hosting model gives every customer real local code execution as
their own uid (PHP/LSAPI, cron, per Phase 8's web terminal), and `ufw`'s
default rule set unconditionally accepts all loopback traffic (confirmed:
`iifname "lo" ... accept` in `ufw-before-input`, with no `-m owner` rule
anywhere), any hosting customer's own PHP/cron process can reach
`127.0.0.1:8088` directly and impersonate any other account.

**IMPACT**: Complete cross-tenant compromise reachable from ordinary
customer-level code execution — a customer with a PHP script or cron job
can bypass `boron-api`'s session auth and the `fb.open` audit trail
entirely, and read/write/delete any other customer's entire home directory,
by sending a raw HTTP request to the loopback port with a forged
`X-Fb-User: <victim-username>` header. This is the exact class of
cross-tenant breach the whole shared-hosting isolation model exists to
prevent, and it requires no bug in the proxy or in FileBrowser itself — it
uses the intended proxy-auth mechanism exactly as designed, from an
unintended caller.

**FIX**: Code-level fix applied. `daemon/filebrowser.py` gained a new
`restrict_backend_access()` function: an idempotent iptables `OUTPUT`-chain
rule pair (`-m owner --uid-owner <boron-api uid> -j ACCEPT` followed by
a catch-all `REJECT` for the backend port), installed automatically on every
`fb.bootstrap` call — which already runs at every daemon startup
(`daemon/server.py:745`) — so it self-heals across restarts without needing
`iptables-persistent`, matching this project's "self-healing on daemon
start" convention. Regression tests cover installation, idempotency
(checked via `-C`, which is position-independent), correct
ACCEPT-before-REJECT ordering, and a missing-service-user case that logs
rather than crashes daemon startup.

**Two rounds of live verification were needed to get this right — recorded
here in full since the first round produced a false sense of security.**
The initial version appended the rules (`-A OUTPUT ...`), which the audit's
own findings write-up (and this section, in its first draft) documented as
the fix. When later asked to apply the equivalent commands live, they were
installed successfully but **had zero actual effect**: `curl` as three
different local uids (the `boron-api` service user, a real hosting
account, and root) all still reached the backend. Root cause: `ufw`'s own
baseline `ufw-before-output` chain contains `ACCEPT ... out lo` as its
*first* rule — unconditionally accepting all loopback traffic — and that
chain is jumped to near the top of `OUTPUT`, long before anything appended
to the *end* of `OUTPUT` is ever evaluated. **Appending to `OUTPUT` is a
no-op for loopback traffic on this box.** The fix: insert the rules at
positions 1 and 2 of `OUTPUT` instead (`iptables -I OUTPUT 1 ...` / `-I
OUTPUT 2 ...`), ahead of ufw's own chain jumps. Re-verified live afterward
with the same three-uid test: `boron-api` → succeeds; the real hosting
account and root → connection refused. `daemon/filebrowser.py` and its
tests were updated to match (insert, not append) so the code now matches
what was actually verified to work, not what merely looked plausible.

**Live status: APPLIED AND VERIFIED on this box** (2026-07-11, on the
user's explicit direction naming this exact live change). Current live
`OUTPUT` chain, positions 1-2:
```
1  ACCEPT  tcp  --  0.0.0.0/0  127.0.0.1  tcp dpt:8088 owner UID match <boron-api uid>
2  REJECT  tcp  --  0.0.0.0/0  127.0.0.1  tcp dpt:8088 reject-with tcp-reset
```
Verified: `boron-api` (uid 996) → HTTP 301 (normal proxy response);
a real hosting account uid and root → `curl: (7) Failed to connect...`
(rejected). `boron-api`/`boron-provisiond`/`boron-filebrowser`
all confirmed still active and healthy afterward (`GET /healthz` → 200).
This live application predates the corresponding code fix landing via a
real deploy — a future `fb.bootstrap` run (next deploy + daemon restart)
will find the rules already present (`-C` matches) and this is a no-op,
so there is no conflict. Separately, cleaned up two harmless, empty,
root-owned directories (`/home/attacker_forged_user`,
`/home/nosuchacct12345`) that were an unintended side effect of the
investigating agent's earlier non-mutating GET test (FileBrowser's
`createUserDir` auto-provisions a directory even for a bare unauthenticated
GET) — confirmed empty before removal via `rmdir`, no account/domain/DB
data was affected.

### A3-8 — High — FileBrowser Quantum's systemd unit has zero sandboxing beyond running as root

**LOCATION**: `deploy/boron-filebrowser.service`; live unit at
`/etc/systemd/system/boron-filebrowser.service`.

**DESCRIPTION**: Confirmed live via `systemd-analyze security
boron-filebrowser.service`: every hardening control
(`NoNewPrivileges`, `ProtectSystem`, `CapabilityBoundingSet` restriction,
`RestrictNamespaces`, `SystemCallFilter`, etc.) is absent — exposure score
9.6/10 "UNSAFE". This is a deliberate, documented tradeoff (the process
needs unrestricted `/home` access across all accounts, so `ProtectHome`
can't be used), but several hardening directives that are compatible with
full `/home` access and don't narrow the feature (`NoNewPrivileges`,
`ProtectKernelModules`, `ProtectKernelLogs`, `ProtectClock`,
`ProtectHostname`, `ProtectControlGroups`, `RestrictSUIDSGID`,
`RestrictNamespaces`, `LockPersonality`, `MemoryDenyWriteExecute`, a
trimmed `CapabilityBoundingSet` dropping `CAP_SYS_MODULE`/`CAP_SYS_BOOT`/
`CAP_SYS_TIME`/`CAP_NET_ADMIN`/etc.) are simply not applied at all.

**IMPACT**: If FileBrowser Quantum (a third-party, actively-developed Go
binary) is ever found to have an RCE or severe path-traversal bug, the
blast radius is unconstrained root on the whole VM, not merely "root over
/home" — no seccomp/capability/namespace restriction limits it even in
principle.

**FIX**: Applied. Added the `/home`-compatible hardening subset listed
above to the unit.

---

---

## Area 11 — Rate limiting, and Area 12 — Branding upload

**Area 11 Q1 (X-Forwarded-For spoofing)**: no finding. The limiter keys
strictly on `request.client.host` (the real TCP peer, populated by uvicorn
from the socket) — confirmed `deploy/boron-api.service` runs uvicorn
with no `--proxy-headers`/trusted-proxy flags, and `boron-api` has no
reverse proxy in front of it (`ARCHITECTURE.md §2`: binds `0.0.0.0:9443`
directly). `X-Forwarded-For` is never read anywhere in `api/`/`daemon/`.

**Area 11 Q2 (limits before auth)**: no finding. Independently re-derived
Starlette's actual middleware wrapping order from the installed framework
source (not assumed from comments) and confirmed `_rate_limit` is more
outer than `_ip_whitelist` and every route handler — a 429 returns before
the daemon's per-username lockout counter is ever touched.

**Area 11 Q3 (interaction with Audit-1 lockout)**: complementary, not
redundant — the two mechanisms cover orthogonal axes (one-IP/many-usernames
vs. many-IPs/one-username) — but exposed the IPv6 finding below.

**Area 12 (branding)**: path traversal — no finding (on-disk filename is
always server-generated from a fixed `{field}.{ext}` pattern, never client
input). Served content-type — no finding (`image/svg+xml` always correct
since `ext` is server-derived; `X-Content-Type-Options: nosniff` applies
unconditionally). SVG XSS — see A3-9 below.

### A3-9 — Medium — Branding SVG filter had two confirmed XSS bypasses via entity encoding

**LOCATION**: `daemon/branding.py:43` (`_SVG_DANGEROUS_RE`, raw
substring match, no entity decoding).

**DESCRIPTION**: Two working bypasses were empirically confirmed by
executing the real function against crafted payloads: (1)
`xlink:href="&#106;avascript:alert(1)"` (a numeric character reference for
`j`) — accepted, because the raw bytes never contain the literal substring
`javascript:`, even though a browser decodes and executes it as one; (2)
`<foreignObject><iframe srcdoc="&lt;script&gt;alert(1)&lt;/script&gt;"/></foreignObject>`
— accepted for the same reason (the entity-encoded `<script>` only becomes
real markup after the browser decodes the `srcdoc` attribute value and
parses it as a fresh document). Both contradict `docs/STATUS.md`'s framing
of "complete" `<script>`/`on*=`/`javascript:`/`foreignObject` rejection —
the filter's own code comment was more honest ("not a full sanitizer, just
a reject-obvious-cases guard").

**IMPACT**: Upload is `require_admin`-gated, and in the app's *current* UI
the asset is only ever rendered via `<img>`/`<link rel="icon">`
(script-non-executing contexts) — but `GET /api/v1/branding/logo`/`favicon`
are public, unauthenticated, and directly navigable (typed URL, "open image
in new tab", a shared link), which bypasses that mitigation and leaves
`script-src 'none'` CSP as the sole remaining defense for anyone who opens
the raw asset URL directly. A malicious or session-hijacked admin could
plant a payload targeting other admins/customers this way.

**FIX**: Partially applied now, full fix deferred. Added `foreignObject` to
the direct reject list (zero legitimate use in a branding logo, safe to
block outright) and a second pass that XML/HTML-entity-decodes the file
(`html.unescape`) and re-runs the same dangerous-pattern check against the
decoded text — this closes both confirmed bypasses without a new
dependency (verified via new regression tests, including a
harmless-entity false-positive check). DEFERRED (documented, not fixed this
pass): a real fix is an XML-aware allowlist sanitizer (parse with
`defusedxml`/`lxml`, walk the DOM, reject `script`/`on*`/non-fragment
`use`/non-image `data:`/`javascript:` URIs in *decoded* attribute values,
re-serialize rather than storing original bytes) — meaningfully over the
10-minute bar to build and verify safely in this pass. The related Low
finding (`<use href="...">` not checked at all for external references) is
covered by the same deferred fix; not independently patched given the risk
of an imprecise regex breaking legitimate `<use>` fragment-reference logos
(a real, if uncommon, branding-logo pattern).

### A3-10 — Medium — Login rate limiter's exact-IPv6-address keying let one attacker rotate through a routed prefix to defeat it

**LOCATION**: `api/ratelimit.py:180,186` (formerly `f"{tier}:ip:{client_ip
or 'unknown'}"`, no CIDR aggregation).

**DESCRIPTION**: IPv6 addresses are commonly allocated to a single
customer/attacker as a routed `/56` or `/64` prefix by residential/mobile
ISPs — no botnet needed to get a virtually unlimited pool of distinct
source addresses. Since this is exactly the "one IP, many usernames"
credential-stuffing scenario the new limiter exists to close (per the Q3
cross-check with the Audit-1 per-username lockout), address rotation
defeated both mechanisms simultaneously: a fresh IPv6 address per attempt
resets the per-IP bucket, and never repeating a username avoids the
per-username lockout.

**IMPACT**: An attacker with ordinary IPv6 connectivity (not a botnet)
could perform unthrottled credential stuffing against `/login`, defeating
the very protection this feature was built to add. IPv4-only attackers were
largely unaffected (residential IPv4 is NAT'd to one address per
household).

**FIX**: Applied. Added `_ip_bucket()` to `api/ratelimit.py`: IPv6 addresses
are normalized to their `/64` network before being used as a rate-limit
key (the standard ISP-routed allocation unit), so every address within one
customer's typical allocation shares one bucket; IPv4 is left as a literal
address (no equivalent aggregation unit at that granularity, and NAT
already aggregates most residential IPv4). Covered by new regression tests
(same `/64` → shared bucket; different `/64` → independent buckets).

---
