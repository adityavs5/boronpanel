# Forgehost — Security Audit 2 Threat Model

Written **before** any Audit 2 fixes, per the audit goal's mandatory pre-work.
Companion to `docs/AUDIT2-FINDINGS.md`.

Scope: the **new attack surface added since Audit 1**. Audit 1
(`docs/AUDIT-THREATMODEL.md` / `docs/AUDIT-FINDINGS.md`) covered the codebase
as of Phase 4. Since then the following shipped and are the subject of this
audit:

- **React SPA frontend** (UI revamp, replaces the server-rendered Jinja2 UI
  for the interactive panel; the Jinja `ui_router`s are now dead code, only
  the JSON `api_router`s are mounted).
- **Node.js / Python app hosting** (Phase 7a-1/2) — one systemd unit per app,
  reverse-proxied by OLS, running as the account's own uid.
- **Per-account Redis** (Phase 7a-3) — one Unix-socket-only redis-server per
  account, systemd-supervised.
- **Per-domain LSCache** (Phase 7a-4).
- **Namespace isolation** (Phase 6b) — per-uid OLS namespace gating via
  `lsnsctl`.
- **cPanel/WHM backup import** (Phase 7b-1) — admin-only, runs as root.
- **Email notifications** (Phase 7b-3).
- **Outbound webhooks** (Phase 7b-4).
- **Usage alerts** (Phase 7b-5) and **bandwidth graphs** (Phase 7b-2).
- **Staging environments** (Phase 7b-6) — one-click domain-to-staging clone.
- **TOTP 2FA** (Phase 5-10) and the surrounding auth changes.

This document does not re-derive the base trust model — it inherits Audit 1's
actors/boundaries (`docs/AUDIT-THREATMODEL.md` §1-3) unchanged and states only
what the new surface *adds* to each.

## 1. Inherited model (unchanged)

Actors, trust levels, and the six trust boundaries from Audit 1 §2 still hold
verbatim. The two load-bearing invariants this audit re-leans on:

- **B3 — `forgehost-api` ↔ `forgehostd` (Unix socket):** the daemon still does
  **not** re-derive authorization; it trusts that the API layer gated the RPC.
  Every new feature adds new RPC ops and new routers, so "a router that forgets
  a `require_*_access` call is a silent cross-account compromise" is again the
  single highest-likelihood place for the next bug. Audit 2 re-swept every new
  router for this (see §3, area 1-10).
- **B4 — one account ↔ another:** Linux DAC (per-uid), the docroot ACL, and now
  additionally **the systemd `User=`/`Slice=` boundary for app units**, **the
  per-account Redis socket's `unixsocketperm 700`**, and **the OLS namespace
  gate**. A break in any of these is a horizontal-escalation finding.

## 2. What the new surface changes about the attack model

### 2.1 New places customer-controlled input reaches a privileged sink

The recurring root-cause class in this codebase (CVE-2024-51567 lineage:
untrusted string → privileged sink without validation) has new sinks:

| New sink | Reached via | Primary defense |
|---|---|---|
| systemd unit file text (`ExecStart`, `User=`, `Description=`) | Node/Python app `entry_point`, `name`, `username`, env vars | `validate_app_entry_point` / `validate_python_entry_point` / `validate_app_name` reject newlines & metacharacters; env values reject `\n`/NUL in **both** `validate_env_vars` and `write_env_file` |
| `mysqldump <name>` run as the MariaDB admin | staging source DB name (read from the account's own `wp-config.php`) | **must** be constrained to a DB the account actually owns — this is the load-bearing check |
| `lsnsctl --uid <n> …` run as root | namespace enable/disable | `uid` is an `int` from the DB, argument-list exec; `username` never reaches the CLI (resolved to a uid via DB lookup) |
| `tarfile.extractall` as root | cPanel import tarball | `filter="data"` (rejects `..`/absolute/device members); size must be bounded before extraction |
| Outbound HTTP POST from root daemon | webhook URL (admin-configured) | URL must resolve to a **public** address (SSRF); no internal/loopback/link-local/metadata targets |
| SMTP header (`To`/`From`/`Subject`) | notification recipient/sender | `validate_email_address` (anchored regex, no newline); body is `set_content` plain text (no template engine) |

### 2.2 New data-at-rest and data-in-transit exposure

- **App env vars** — customer secrets (API keys etc.) → encrypted at rest with
  Fernet (`daemon/appcrypto.py`), decrypted only into a root-only (0600)
  systemd `EnvironmentFile`. Never written where the account's own uid can read
  the plaintext.
- **Webhook payloads** — leave the trust boundary entirely (POSTed to an
  external, admin-chosen URL) **and** persist in `WebhookDelivery.payload`.
  Whatever context an event carries is exposed to a third party and to any
  reader of the control-plane DB. Event context that includes a secret (e.g.
  the initial account password on `account.created`) is therefore a
  credential-disclosure vector — a first-class concern for this audit.
- **Webhook signing secret / TOTP secret** — stored plaintext at rest (both
  must be *used*, not merely compared, so neither can be one-way hashed);
  guarded only by the DB file's `root:forgehost-api 0640` permission. Accepted
  tradeoff, consistent with existing posture, documented not fixed.

### 2.3 New DoS surface

- Per-account systemd units and Redis instances consume real host resources
  bounded by the account's cgroup slice; the local app port range
  (30000-31999) is dedicated and disjoint from the panel (9443) / PowerDNS
  (8081) / mail ports, so an app can never squat a control-plane port.
- `/run/redis` is world-writable (mode 1777, `/tmp`-style) so each account's
  own redis-server can create its socket — introduces a socket-name-squatting
  DoS vector between accounts (one account pre-creating another's socket path).
- cPanel import extraction and staging DB clones run as root outside any
  account quota — disk-exhaustion surface if unbounded.

### 2.4 Frontend-specific model (new)

The SPA moves rendering to the client but **not** the security boundary. The
authoritative access control is still the API (`get_identity` +
`require_*_access` on every route). Client-side artifacts (the persisted
`role`/`username` in `localStorage`, `ProtectedRoute`) are UX only — tampering
with them changes what the SPA *renders*, never what data the API *returns*.
XSS is the class that would break this model (a script with the user's
same-origin cookie can drive the API as them); the audit therefore treats
`dangerouslySetInnerHTML`, unescaped error rendering, and the CSP as the SPA's
load-bearing controls. Session state is an httpOnly signed cookie, not a
localStorage JWT — a stored-XSS payload cannot read it.

## 3. Attacker goals this audit specifically tests (per area)

1. **React frontend** — inject script (stored/reflected XSS) that runs with the
   victim's session; read a token/session from client storage; reach an
   admin-only view's *data* without admin auth; leak a token to the console.
2. **Node/Python hosting** — inject a systemd directive (e.g. `User=root`) via
   a crafted entry point/env var; collide/steal another account's port; gain
   any capability beyond the account's own uid; traverse out of the app's log
   path.
3. **Redis** — reach another account's socket; point the socket outside
   `/run/redis`; escape the memory cap; retain access across suspend/unsuspend.
4. **Staging** — clone or target **another account's** database as the source;
   escape the account home on file copy; scope a staging domain to a domain the
   account doesn't own; write a rewritten `wp-config.php` outside the staging
   docroot.
5. **cPanel import** (root) — tar-slip a file outside the account home; inject a
   command via a domain/username/DB name parsed from the tarball; exhaust disk
   before quotas apply; leak an extracted password to a log.
6. **Webhooks** — SSRF the root daemon at an internal/loopback/metadata
   endpoint; exfiltrate a secret carried in an event payload; read a
   URL/secret/payload stored plaintext.
7. **LSCache** — poison another account's cache via a shared/collidable cache
   root; escape the per-vhost cache path.
8. **Namespace isolation** — inject a command via `username`/`uid` into
   `lsnsctl`; lower `min_uid` through the API; perform a file operation that
   bypasses the namespace.
9. **Email notifications** — inject an SMTP header via `To`/`Subject`; inject a
   template expression via a customer-controlled value.
10. **Auth re-audit** — bypass an enabled 2FA on an admin endpoint; fixate/reuse
    a session across a password change; reach an account switcher as a customer;
    obtain more scope than an API token was issued with.

## 4. Severity rubric (unchanged from Audit 1)

- **Critical** — root on the host, or cross-account data/credential compromise
  reachable by an authenticated customer (the class Phase 4-0b proved real at
  scale).
- **High** — credential disclosure across a trust boundary, or a
  vertical/horizontal escalation with a meaningful precondition.
- **Medium** — defense-in-depth gap or DoS reachable by an authenticated actor;
  admin-gated exposure of a root-context action.
- **Low/Info** — hardening gap, accepted tradeoff, or requires an already-
  privileged precondition.

## 5. Explicitly out of scope (unchanged)

No new features, no style refactoring; multi-server/WHM, reseller billing, and
commercially-licensed OLS capabilities remain out of scope. 2FA is audited for
bypass, not redesigned. Carry-forward deferrals from Audit 1 (F14 CSRF token,
F16 token expiry) are re-noted where the new surface touches them, not
re-litigated.
