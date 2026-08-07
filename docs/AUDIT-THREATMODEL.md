# Boron — Security Audit Threat Model

Written before any audit fixes, per the audit goal's mandatory pre-work.
Scope: a full-codebase security re-audit of Boron as it stands after
Phase 4 (12 features + the cross-account IDOR fix + the password-strength
audit). This is not a redesign document — it exists to make the trust
boundaries and attacker classes explicit before findings are triaged, so
severity ratings in `AUDIT-FINDINGS.md` are judged against a stated model
rather than intuition.

## 1. Actors

| Actor | Capability | Trust level |
|---|---|---|
| **Anonymous internet user** | Can reach `:80`/`:443` (hosted sites), `:9443` (panel login page), `:21`/`:25`/`:587`/`:993`/`:143` (FTP/mail), `:53` (DNS) | Untrusted |
| **Hosting customer** | Valid `PanelUser` session/token scoped to exactly one `Account` | Semi-trusted: authenticated, but must be contained to their own account only |
| **Panel admin** | Valid `PanelUser` session/token with `role=admin` | Fully trusted for provisioning actions; still not root on the OS |
| **Provisioning daemon (`borond`)** | Runs as root, the only process that mutates system state | Fully trusted, the actual privilege boundary |
| **REST API (`boron-api`)** | Runs unprivileged, read-only DB access, talks to the daemon only over the RPC socket | Trusted to gate authorization correctly — see §3, this is the load-bearing assumption the whole model leans on |
| **A hosted PHP application** (WordPress/Joomla/etc., or arbitrary customer-uploaded PHP) | Executes as the account's own Linux uid via LSAPI | Untrusted code, contained by Linux DAC + cgroups, not by Boron's own logic |

## 2. Trust boundaries

1. **Internet ↔ boron-api (`:9443`)** — the primary web attack surface.
   Every request is unauthenticated until `get_identity` resolves a session
   cookie or bearer token.
2. **Internet ↔ hosted vhosts (`:80`/`:443`)** — arbitrary customer PHP code
   runs here; a compromise of one hosted site must not escalate to another
   account or to the panel itself.
3. **boron-api ↔ borond (Unix socket)** — the privilege boundary
   between "can ask for a privileged action" and "can actually perform one."
   **Load-bearing assumption**: the daemon does not re-derive authorization;
   it trusts that any RPC call it receives has already been authorized by
   the API layer (`ARCHITECTURE.md` §2, confirmed unchanged by reading
   `daemon/server.py`'s `dispatch()` — it pops `_actor`/`_role` for the audit
   log only, never for an authorization decision). This was already proven
   fragile once (`CHECKPOINT-phase4-0b`: 8 routers missing the check, not
   caught by the daemon). Any future router that forgets a
   `require_account_access`/`require_domain_access` call is a full
   cross-account compromise with **no second gate** to catch it. This audit
   treats "does the daemon independently verify ownership" as a standing
   architectural risk, not a one-time bug — see AUDIT-FINDINGS.md.
4. **One hosting account ↔ another** — Linux DAC (uid/gid separation),
   OLS's docroot ACL, PHP LSAPI per-account external app, the file
   manager's realpath jail, Pure-FTPd chroot, and cgroups v2 CPU/mem/IO/pids
   limits. A break here is a horizontal-escalation finding.
5. **Hosting customer ↔ panel admin** — `require_admin` gates
   admin-only ops (account create/suspend/terminate/limits, DNS zone
   creation, backup destinations/schedules, API tokens). A customer
   reaching any of these is a vertical-escalation finding.
6. **Boron's own control-plane DB (SQLite) — writer vs. reader** —
   `borond` (root) is the only writer; `boron-api` opens the same
   file read-only, enforced by OS file permissions (0640,
   `root:boron-api`), not just application logic. Any code path that
   assumes the API process can write to this DB is a design bug by
   construction (SQLite will refuse the write at the OS level).

## 3. Assets, ranked by impact if compromised

1. **Root on the host** (via the daemon or a subprocess-injection bug) —
   compromises every hosted account simultaneously. Highest impact.
2. **The `borond` Unix socket / RPC authorization gate** — since the
   daemon trusts the API layer, bypassing or confusing that gate is
   equivalent to admin-level access to every account.
3. **Any single hosting account's data** (files, databases, mail, DNS) —
   cross-account horizontal escalation. Proven previously exploitable at
   scale (Phase 4-0b).
4. **Panel login credentials** (session cookies, bearer tokens, password
   hashes) — session/auth-layer compromise lets an attacker impersonate a
   customer or admin without touching the daemon at all.
5. **Secrets at rest**: `SESSION_SECRET`, MariaDB admin password, PowerDNS
   API key, DKIM private keys, backup destination credentials (rclone).
6. **Availability of the panel and hosted sites** — DoS against the single
   shared OLS/daemon/DB stack affects every tenant at once (no per-tenant
   isolation for the control plane itself).

## 4. Attacker classes this audit specifically considers

- **Unauthenticated internet attacker** hitting `:9443`, `:80/:443`,
  `:21/:25/:587/:993/:143`, and any other publicly bound port — reconnaissance,
  credential attacks against `/login`, injection attempts against any
  endpoint that doesn't require auth.
- **A malicious or compromised hosting customer** — the most important class
  given this project's history: authenticated, but must be unable to read/
  write/delete/impersonate anything outside their own `account_id`. Covers
  IDOR, path traversal, symlink attacks, zip slip, SSRF via any
  customer-influenced URL, command injection via any customer-supplied
  string that reaches a subprocess call, and cgroup/resource-limit escape.
- **A malicious hosted PHP application** — arbitrary code execution as the
  account's own Linux uid is an *accepted* capability of shared hosting
  (that's what an account uid boundary is for); the question is whether it
  can do anything a legitimate customer of that account couldn't already do
  via the panel (i.e., no *additional* privilege from RCE beyond the
  account's own uid).
- **A former (terminated) hosting customer** — should have zero residual
  access to anything once `account.terminate` completes. This audit
  specifically re-examines this because it was flagged twice before
  (Phase 4-0b, Phase 4-12) as an unresolved gap and never fixed.
- **A network attacker (MITM/DNS)** — relevant only to outbound fetches
  (app installer downloads, WordPress/Joomla/Drupal/PrestaShop release
  URLs, ACME callbacks); all of Boron's own inbound traffic is TLS
  where it matters (panel), so this class is scoped narrowly.
- **An operator/insider mistake** — e.g. a config value left at an
  insecure default, a service accidentally bound to `0.0.0.0` instead of
  loopback, a debug endpoint left reachable. Covered by §7 of the audit
  scope (infrastructure exposure), verified against the actual live server,
  not just the code.

## 5. Explicitly out of scope for this audit

Per the goal: no new features, no style refactoring. Also out of scope,
consistent with the project's own stated v1 boundaries
(`ARCHITECTURE.md` §11): multi-server/WHM-style management, reseller
billing, a WAF/IDS layer, and anything requiring a commercially-licensed
OpenLiteSpeed capability. 2FA is explicitly named in the goal as
"document, don't build."

## 6. What prior phases already fixed that this audit re-verifies rather than re-litigates

- The Phase 4-0b cross-account IDOR (8 routers + 2 daemon modules) — every
  router re-checked live in this audit (see AUDIT-FINDINGS.md §2); found
  still fixed, no regression.
- The Phase 3-5 FTP no-chroot finding — re-verified live.
- The recurring plaintext-password-in-argv bug class (3 prior occurrences)
  — every `procutil.run()` call site re-grepped for a fourth occurrence.
- The Phase 3-7 regex-anchor (`^...$` vs `\A...\Z`) bug — re-grepped across
  `shared/validation.py`, including every Phase 4 validator added after the
  original fix.
