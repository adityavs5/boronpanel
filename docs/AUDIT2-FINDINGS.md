# Forgehost — Security Audit 2 Findings

Companion to `docs/AUDIT2-THREATMODEL.md`. Scope: the **new attack surface
added since Audit 1** (React SPA, Node/Python app hosting, per-account Redis,
per-domain LSCache, namespace isolation, cPanel import, email notifications,
webhooks, staging, and the 2FA/auth changes). All 10 focus areas from the
audit goal were audited; each is covered in §1-10 below whether or not it
produced a finding. Findings are listed most-severe first.

Method: every new `daemon/*.py` feature module was read in full for the
injection/traversal/SSRF/secret-exposure classes; every new `api/routers/*.py`
router was re-swept for the missing-ownership-check class that caused Phase
4-0b (verified: all correctly gated — see §10 and the per-router table); the
React `frontend/src` tree was grepped and read for XSS sinks, client-side
credential storage, and console logging.

---

## Section 0: Summary

| # | Severity | Area | Title | Status |
|---|---|---|---|---|
| A2-1 | **Critical** | Staging | Cross-account DB exfiltration via attacker-controlled `wp-config.php` `DB_NAME` | **Fixed** |
| A2-2 | **High** | Webhooks | Account initial password leaked to external webhook + stored plaintext in `WebhookDelivery` | **Fixed** |
| A2-3 | **Medium** | Webhooks | SSRF: root daemon POSTs to any URL, no internal-IP/metadata blocking | **Fixed** |
| A2-4 | **Medium** | cPanel import | Decompression bomb: extraction size unbounded → root-owned disk exhaustion | **Fixed** |
| A2-5 | Low | Redis | Cross-account DoS via socket-name squatting in world-writable `/run/redis` | DEFERRED (design tradeoff) |
| A2-6 | Low | cPanel import | URL-source fetch follows redirects, no internal-IP block (admin-only) | DEFERRED (admin-trusted) |
| A2-7 | Info | Auth | 2FA is opt-in, not mandatorily enforced for the admin role | Documented |
| A2-8 | Info | Frontend | Client-side `ProtectedRoute`/persisted role is cosmetic (server enforces) | Documented (no action) |
| A2-9 | Info | Webhooks | Webhook signing secret & delivery payload stored plaintext at rest | Documented (accepted tradeoff) |
| A2-10 | Info | Frontend | CSRF still relies on `SameSite=Lax` only (carry-forward of Audit 1 F14) | Documented |
| A2-11 | Info | Auth | Bearer API tokens have no expiry (carry-forward of Audit 1 F16) | Documented |
| A2-12 | Info | Node/Python | `entry_point` validators reject `\n` but not a lone `\r` (not exploitable) | Documented (no action) |

Fixed: 1 Critical, 1 High, 2 Medium. All Critical + High + the two explicitly-
in-scope Medium items are remediated and covered by regression tests.

---

## Section 1: React frontend

**XSS** — No `dangerouslySetInnerHTML`, `innerHTML`, `eval`, or `new Function`
anywhere in `frontend/src` (grepped). API error messages flow through
`normalizeError` (`lib/api.js`) and are rendered by React as text nodes
(auto-escaped) — a malicious `detail`/`error` string cannot execute. **No XSS
vector found.**

**Session/token storage** — The interactive panel authenticates with a signed,
`httpOnly`, `Secure`, `SameSite=Lax` cookie (`fh_session`), **not** a
localStorage JWT (`lib/api.js` comment + `security.py`). The zustand store
persists only non-sensitive `role`/`username` to localStorage
(`partialize` in `store/auth.js`); the 2FA `pendingToken` is held only in
memory and is explicitly excluded from persistence. **No credential is stored
where a script could read it.**

**Tokens logged to console** — No `console.*` calls exist anywhere in
`frontend/src`. **None.**

**Unauth access to protected routes** — `ProtectedRoute` gates on the persisted
`role`, but this is UX only; the real boundary is the API (`get_identity` on
every route). Tampering with localStorage renders an admin *shell* but every
data call still 401/403s server-side. See **A2-8 (Info)**.

**CSP** — Correctly scoped in `api/main.py`: `/app/*` gets `script-src 'self'`
(needed for the bundle; no inline/CDN scripts), everything else keeps
`script-src 'none'`. `connect-src 'self'`, `frame-ancestors 'none'`,
`base-uri 'self'`, `form-action 'self'`. No XSS surface introduced by the SPA.

**CSRF** — Unchanged from Audit 1: `SameSite=Lax` + `Secure` + `HttpOnly`
blocks the classic cross-site auto-POST; all mutations use non-GET verbs. See
**A2-10 (Info)**.

## Section 2: Node.js / Python app hosting

**systemd unit injection** — The unit file is built by f-string interpolation
of `ExecStart`/`User`/`Description`/`WorkingDirectory` (`daemon/nodeapps.py`,
`daemon/pythonapps.py`). The critical vector — a newline in `entry_point`
injecting a later `User=root` line (systemd is last-assignment-wins) — is
blocked: `validate_app_entry_point` rejects `\n` and NUL, rejects leading
`/`/`~`, and rejects `..`/empty segments; `validate_python_entry_point` is a
strict `module:callable` regex. `name`/`username`/`node_version`/`app_type`
are all strict allowlist/charset validators; `port` is an `int`. **No unit
injection possible.** (See A2-12 for the benign lone-`\r` note.)

**Env var → unit escape** — Env vars are written to a separate root-only (0600)
`EnvironmentFile`, not the unit. Newlines/NUL in values are rejected in **both**
`validate_env_vars` and again in `appunits.write_env_file` (defense in depth).
Values are Fernet-encrypted at rest (`daemon/appcrypto.py`). **No escape.**

**Port conflicts** — Dedicated range 30000-31999 (`app_port_range_start/end`),
disjoint from the panel (9443), PowerDNS (8081), and mail ports; `allocate_port`
checks both `NodeApp` and `PythonApp` tables so the two kinds never collide.
Apps bind `127.0.0.1`. **Cannot squat a control-plane port.**

**Runs as account user, no elevated caps** — Units set `User=`/`Group=` to the
account and `Slice=forgehost-<username>.slice`; no `AmbientCapabilities`/
`CapabilityBoundingSet` grant. `npm install`/`pip install`/venv creation run
via `runuser -u <username>`. **Confirmed unprivileged.**

**Log viewer traversal** — `get_logs` derives the path from the validated
`name` and an id-scoped row lookup; `tail_log_file` reads that derived path.
No client-supplied path component. **No traversal.**

## Section 3: Redis

**Socket permissions after suspend/unsuspend** — `_render_conf` sets
`unixsocketperm 700`; the socket is created by redis-server running as the
account's own uid (`User=`), so it is 700 + account-owned regardless of
suspend/unsuspend (those flip the vhost/password, not the redis conf). **Isolation
holds across the lifecycle.**

**Custom socket path outside `/run/redis/`** — The socket path is *always*
derived from the validated username (`socket_path()`); there is no
customer-supplied path parameter anywhere. **Not possible.**

**Memory-limit bypass** — `maxmemory <mem_mb>mb` + `allkeys-lru`, `mem_mb`
bounded 16-4096; the account's cgroup slice is a second bound. **Enforced.**

**Finding A2-5 (Low)** — `/run/redis` is mode 1777 (see below).

## Section 4: Staging environments

**Finding A2-1 (Critical)** — cross-account DB targeting (detailed below).

**File copy source escaping home** — `_copy_files` copies from
`domain_row.docroot` (the account's own domain docroot from the DB, not
user-supplied) into the staging docroot from `add_domain`. Both are derived.
**No escape.**

**Staging domain scoping** — `staging.<source_domain>` where `source_domain`
must belong to the account (`_account_and_domain` checks
`Domain.account_id == account.id`); also refuses if the staging domain already
exists. **Scoped to the account's own domains.**

**wp-config rewrite target** — `_rewrite_staging_wp_config` writes to
`<staging_docroot>/wp-config.php`, with `staging_docroot` derived from
`add_domain`. **Within the staging docroot.**

## Section 5: cPanel import

**Tar slip** — `_extract_archive` uses `tarfile.extractall(..., filter="data")`
(rejects absolute paths, `..`, device/special members). Regression test
`test_extract_archive_rejects_path_traversal` confirms. **Blocked.**

**Command injection via tarball names** — `username` (`validate_username`),
domains (`validate_domain`), DB suffix (sanitized + `validate_db_identifier`
in `create_database`), mailbox local-part (`validate_mailbox_local_part`) are
all validated before reaching any path/subprocess. Cron `command`/`schedule`
go into the account's own `crontab -u <user>` (unprivileged, same documented
posture as the cron feature). No `shell=True` anywhere. **No injection.**

**Finding A2-4 (Medium)** — decompression bomb (detailed below).
**Finding A2-6 (Low)** — URL-source fetch (detailed below).

**Extracted passwords logged** — Mailbox/FTP imports generate fresh random
passwords (never the original); wp-config/db rewrite return values contain no
password; `_append_result`/`logger` record only item/status/short detail. The
MariaDB admin credential goes through a temp `--defaults-extra-file` (never
argv). **No password reaches a log.**

## Section 6: Webhooks

**Finding A2-2 (High)** — initial password in payload (detailed below).
**Finding A2-3 (Medium)** — SSRF (detailed below).
**Finding A2-9 (Info)** — URL/secret/payload plaintext at rest (below).

Webhooks are admin-only (`require_admin` on every route, `api/routers/webhooks.py`).

## Section 7: LSCache

**Cache poisoning / per-account scoping** — Each cache-enabled domain gets its
own `storagepath` at `/usr/local/lsws/cachedata/<vhost_name>`, where
`ols._vhost_name(domain) = domain.replace(".", "_")`. Valid domains
(`DOMAIN_RE`) never contain `_`, so two distinct domains can never map to the
same vhost name — the mapping is collision-free (confirmed by reading
`_vhost_name`). `purge`/`get_stats`/`set_settings` all key off the validated
domain. Ownership is gated at the router (`require_domain_access`). **Cache is
strictly scoped per-domain; no cross-account poisoning.**

## Section 8: Namespace isolation

**lsnsctl argument injection** — `enable_uid`/`disable_uid`/`unmount_uid` call
`run([LSNSCTL_BIN, "--uid", str(uid), <verb>])` with `uid` an `int` from the
DB and an argument list (`shell=False`). `username` is validated
(`validate_username`) and resolved to a uid via a DB lookup — it never reaches
the CLI. **No injection.**

**min_uid cannot be lowered via API** — There is **no** `set-min-uid` RPC op
registered (`daemon/server.py` exposes only enable/disable/status/bulk_enable/
health_summary); `get_min_uid` is read-only. **The API cannot lower the floor.**

**File ops bypassing the namespace** — The namespace is OLS mount-only
isolation for LSAPI workers; the root daemon's own file operations are
unaffected by design (it is the privileged process). No new bypass. **Sound.**

## Section 9: Email notifications

**Header injection (To/From/Subject)** — `_send_email` uses stdlib
`EmailMessage`; `To`/`From` come from `validate_email_address` (anchored regex,
no newline possible) and `Subject` from a static per-event map (or
`f"...: {event_type}"` with `event_type` ∈ the validated allowlist). **No CRLF
header injection.**

**Template injection** — The body is built by plain f-string interpolation and
set via `msg.set_content(body)` — no template engine (no Jinja2/`format`/eval),
so customer-controlled values are inert text. `username` is validated anyway.
**No template injection.** (Note: the `account.created` body includes the
initial password by design — sent to the account owner's own mailbox; the
*webhook* leak of the same value is A2-2.)

## Section 10: Auth layer re-audit

**2FA bypass** — At login, if TOTP is enabled the password step returns
`needs_2fa` + a short-lived (5-min) signed pending token and **does not create
a session** (`login_submit`); the session cookie is minted only in
`_complete_login`, which runs only after `/login/2fa` verifies the code. The
pending token is a different itsdangerous salt and is never accepted by
`get_identity`. **An enabled 2FA cannot be bypassed.** (2FA is opt-in for
admins — A2-7.)

**Session fixation** — `create_session` mints a fresh `secrets.token_urlsafe(32)`
per login; there is no pre-auth session to upgrade. Password change
(`set_panel_user_password`, Audit 1 F11) revokes every session for the user.
**No fixation; old sessions die on password change.**

**Account switcher for customer role** — No impersonation/act-as/switch
endpoint exists (grepped `api/` + `frontend/`). `useAccountUsername` reads a
route param for admins managing an account; a customer is still restricted by
`require_account_access` to their own account. **No customer-reachable
switcher.**

**API token over-permissioning** — Tokens are admin-issued only
(`tokens.py` is `require_admin`); role must be `admin|customer`; an
account-scoped token's `account_id` is enforced by `require_account_access` on
every route. A customer cannot mint or escalate a token. **Scoped correctly.**
(No expiry — A2-11.)

**Route ownership sweep** — All 10 new routers (`nodeapps`, `pythonapps`,
`redis_router`, `lscache_router`, `staging`, `notifications`, `usage_alerts`,
`bandwidth`, `twofactor`, `usage`) were re-audited: every route performs the
correct `require_admin`/`require_account_access`/`require_domain_access` check
**before** any `call_daemon`/data access, keyed on the same identifier the
action targets. The daemon additionally scopes per-app lookups by
`account_id` (`_get_row`). **No new IDOR found** (the Phase 4-0b class has not
regressed).

---

## Detailed findings

### A2-1 — Critical — Staging: cross-account database exfiltration via `wp-config.php` `DB_NAME`

**LOCATION**: `daemon/staging.py` — `_source_db_name` +
`_clone_database_for_staging` (create path) and `sync_staging` (sync path);
`daemon/backup.py:_dump_database`.

**DESCRIPTION**: When cloning a WordPress site to staging, the source database
name is read directly from the account's own `wp-config.php`
(`_source_db_name`), which the account can freely rewrite (file manager, FTP,
its own PHP running as its uid). That name is passed to
`backup._dump_database`, which runs `mysqldump` as the MariaDB **admin**
(`forgehost_daemon`) — a credential with access to *every* database on the
instance. There was no check that the named database belongs to the account.

**IMPACT**: An authenticated customer sets `define('DB_NAME', 'victim_wpdb')`
(or the internal `forgehost_mail` schema) in their own docroot's
`wp-config.php`, calls staging create/sync for their own domain (which passes
the correct `require_account_access`/`require_domain_access` gates — the flaw
is below the ownership boundary), and staging dumps the victim's database and
restores it into a staging database the attacker fully controls (own grant +
password). The attacker then reads the entire contents via their staging site
or a direct DB connection — **full cross-account (and mail-schema) database
disclosure, reachable by any customer.** Highest-impact class per this
project's own history (Phase 4-0b).

**FIX**: Added `_assert_source_db_owned_by_account(username, db_name)`, called
on both the create and sync paths immediately after `_source_db_name` and
before any dump. It requires the source DB to be present in `DatabaseGrant`
for the acting account — `DatabaseGrant` is the authoritative record of which
databases an account owns (every account DB is created through
`handlers_database.create_database`, which writes exactly one grant row). A
`wp-config` naming any other database is rejected with a clear error and
nothing is dumped. Regression test
`test_create_staging_rejects_foreign_source_database` proves a foreign source
DB is refused and never dumped; existing WordPress-clone tests updated to
record the (legitimate) grant they rely on.

### A2-2 — High — Webhooks: initial account password leaked to external endpoint + stored plaintext

**LOCATION**: `daemon/webhooks.py:maybe_trigger`; emitted from
`daemon/server.py:399` (`events.emit("account.created", account,
initial_password=...)`); `account.created` ∈ `WEBHOOK_EVENT_TYPES`
(`shared/models.py:954`).

**DESCRIPTION**: `events.emit` fans every lifecycle event to both the
notification-email channel and the webhook channel with the same `context`.
`account.created` carries the new account's **initial plaintext password** (so
the email channel can send it to the account owner). `maybe_trigger` built the
webhook payload as `dict(context)`, so the password was (a) POSTed to whatever
external URL an admin configured for that event and (b) persisted in
`WebhookDelivery.payload` (a JSON column, plaintext in the control-plane DB —
readable by `forgehost-api`, which otherwise only ever sees hashed
credentials).

**IMPACT**: A live account credential crosses the panel's trust boundary to a
third-party system (e.g. a billing integration that has no need for it) and
persists in plaintext at rest — a credential-disclosure and secrets-at-rest
violation of this project's "passwords never logged/stored" rule. Requires an
admin to have configured a webhook subscribed to `account.created`; the value
is the change-on-first-login initial password.

**FIX**: `maybe_trigger` now strips a denylist of sensitive context keys
(`initial_password`, `password`, `new_password`, `secret`, `token`,
`api_token`, `recovery_codes`) before building the payload — enforced at the
webhook boundary so it holds for every current and future event, while the
email channel (the legitimate recipient of the initial password) is unchanged.
Regression tests assert the secret never reaches the persisted payload and
that unrelated fields pass through.

### A2-3 — Medium — Webhooks: SSRF (no internal-IP / metadata blocking)

**LOCATION**: `daemon/webhooks.py:_deliver`; `shared/validation.py:validate_webhook_url`.

**DESCRIPTION**: `validate_webhook_url` checked only scheme/netloc/control
characters; `_deliver` then made `httpx.post(url, ...)` from **forgehostd
(root)** with no restriction on the destination address. An admin (or an
over-scoped admin API token) could point a webhook at an internal-only service
(`http://127.0.0.1:8081` PowerDNS, other loopback services) or the cloud
metadata endpoint (`http://169.254.169.254/`).

**IMPACT**: Server-Side Request Forgery from a root process into the trusted
internal network — blind (only status code/error are stored), but usable for
internal reachability probing and firing unauthenticated POSTs at internal
services. The audit goal explicitly requires these ranges be blocked.

**FIX**: Two-layer guard. (1) Creation/update time: `validate_webhook_url`
rejects a literal internal IP host (private/loopback/link-local/reserved/
multicast/unspecified) for immediate feedback. (2) Delivery time
(authoritative): `_assert_public_destination` resolves the host via
`getaddrinfo` and refuses if any resolved address is non-public — this also
catches hostname targets and DNS-rebinding (a name that was public at creation
but resolves internal at delivery). `httpx.post` does not follow redirects, so
a 3xx to an internal target cannot bypass it. A blocked destination is a
terminal, recorded failure with **no** outbound request made. Regression tests
cover literal-IP rejection at creation, the resolver guard across all internal
ranges, and the no-request-made delivery path.

### A2-4 — Medium — cPanel import: decompression bomb → root-owned disk exhaustion

**LOCATION**: `daemon/cpanel_import.py:_extract_archive`;
`shared/config.py:cpanel_import_max_extracted_bytes`.

**DESCRIPTION**: The upload/download path caps the *compressed* size at 10GB,
but extraction (`tarfile.extractall`) had no bound on the *decompressed* size.
A `tar.gz` decompression bomb (small compressed, enormous expanded) extracted
as root into `cpanel_import_staging_dir` — outside any account quota — could
exhaust the host disk, a whole-server DoS affecting the control-plane DB and
every tenant. Admin-initiated, but the tarball content is untrusted (an
imported cPanel backup may come from a departing/hostile customer).

**IMPACT**: Disk-exhaustion DoS "before limits enforced" (exactly the goal's
wording for this area), executed as root.

**FIX**: `_extract_archive` sums the declared regular-member sizes from the tar
index (reading the index does not decompress the data — and the declared sizes
are precisely what `extractall` will write) and refuses if the total exceeds
`cpanel_import_max_extracted_bytes` (50GB default — generous vs. a real
quota-bounded cPanel account, restrictive vs. a multi-TB bomb) before writing
anything. Regression tests cover the bomb-rejection and normal-size paths.

---

## Deferred / documented (not fixed — with reasoning)

### A2-5 — Low — Redis cross-account DoS via `/run/redis` socket-name squatting

`/run/redis` is world-writable (mode 1777, `/tmp`-style) because each account's
own redis-server (running as that uid) must be able to create its socket there;
per-file isolation (`unixsocketperm 700` + account ownership on bind) is the
real boundary. A consequence: an account that can run code as its own uid (a
PHP script, a Node/Python app) can pre-create `/run/redis/<victim>.sock` (a
file or symlink); the sticky bit then prevents the victim's redis from
`unlink()`-ing it, so the victim's redis fails to bind and start. **DEFERRED**:
this is DoS-only (no data crosses accounts — the 700 perm still holds), requires
own-uid code execution (an accepted capability), and is partially mitigated by
Ubuntu 24.04's default `fs.protected_regular`/`fs.protected_symlinks`. A real
fix (per-account `/run/redis/<user>/` subdirectory owned 700 by the account)
is a provisioning change beyond this audit's non-feature scope; recommended as
a future hardening item.

### A2-6 — Low — cPanel import URL source: redirect-following, no internal-IP block

`_obtain_archive` fetches an admin-supplied backup URL with
`httpx.stream(..., follow_redirects=True)` and no internal-IP restriction — a
narrow SSRF. **DEFERRED**: admin-only (admins are trusted for provisioning),
and unlike the webhook case the URL is an operator deliberately naming a backup
source that may legitimately be an internal backup server, so blanket
internal-IP blocking here could break intended use. Noted for operator
awareness; the response body is written to a staging file (not reflected to the
attacker), bounding exfiltration.

### A2-7 — Info — 2FA is opt-in, not mandatorily enforced for admins

An admin who has not enabled TOTP logs in with password only; an admin who
*has* enabled it cannot be bypassed (A2-10). Making 2FA mandatory for the admin
role would lock out any pre-existing admin credential that predates the feature
— the documented Phase 5-10 decision. Policy/UI concern, not a login-time
bypass. Recommended future item: an admin-role "2FA required" enrollment gate.

### A2-8 — Info — Client-side route guard is cosmetic

`ProtectedRoute` and the persisted `role` are UX only; the authoritative
boundary is the API. Tampering with localStorage changes what the SPA renders,
never what data the API returns (every route enforces `get_identity` +
`require_*_access`). No action — this is the correct SPA security model.

### A2-9 — Info — Webhook signing secret & delivery payload stored plaintext at rest

`Webhook.secret` (HMAC signing key — must be *used*, not compared, so cannot be
one-way-hashed) and `WebhookDelivery.payload` are plaintext in the DB, guarded
by the DB file's `root:forgehost-api 0640` permission — the same accepted
tradeoff as `TotpCredential.secret`. The high-value case (a live credential in
the payload) is closed by A2-2's secret-stripping. Not further changed.

### A2-10 — Info — CSRF relies on `SameSite=Lax` only

Carry-forward of Audit 1 **F14**. `SameSite=Lax` + `Secure` + `HttpOnly`
mitigates the classic cross-site POST; all state changes use non-GET verbs. An
explicit anti-CSRF token remains a recommended hardening item for the
highest-value mutations. Not re-litigated here.

### A2-11 — Info — Bearer API tokens have no expiry

Carry-forward of Audit 1 **F16**. Long-lived machine tokens with manual
revocation are the intended billing-integration posture. Unchanged.

### A2-12 — Info — App entry-point validators reject `\n` but not a lone `\r`

`validate_app_entry_point` rejects `\n`/NUL but not a bare `\r`. systemd's unit
parser splits logical lines on `\n` (a mid-value `\r` is retained, not treated
as a line break), so a lone `\r` cannot start a new `User=`/`ExecStart`
directive — **not exploitable**. Noted for completeness; no change made.
