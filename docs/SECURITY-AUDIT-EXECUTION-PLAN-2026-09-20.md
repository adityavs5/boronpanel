# BoronPanel security audit and remediation execution plan

Prepared: 2026-09-20. Planner: Astra. Intended executor: GPT Sol.

Status: execution in progress on `security-audit-2026-09-20`. See `CHECKPOINT-security-audit-2026-09-20.md` for verified fixes and open release blockers. This original plan remains the completion standard; a live hotfix is not a completed security release.

## 1. Objective and completion standard

Audit the complete hosting control panel and its deployed security boundaries; fix confirmed vulnerabilities; prove the fixes with adversarial regression tests; deliver a tested security release and an accurate residual-risk report. Cover source, build/install/update paths, Ubuntu deployment, integrations, customer workloads, and administrative operations. Preserve the Evo/Paper interfaces and ordinary hosting workflows.

Use OWASP ASVS 5.0.0 Level 2 as the application baseline, with explicitly selected Level 3 requirements for privileged administration, authentication, isolation, secrets, and updates. Map applicable requirements and exclusions individually. This is an engineering verification target, not a claim of certification or full Level 3 conformance. Use WSTG v4.2 for repeatable web testing and add hosting-specific Linux and root-daemon tests that web standards do not cover.

Completion requires:

- Every route, RPC operation, scheduled/background entry point, privileged helper, external listener, and installer/update component inventoried and given a review disposition. Public routes must be deliberately identified, not silently excluded.
- Every confirmed finding tracked from reproduction through correction and retest. Fix actionable findings of every severity; do not use a short time limit to defer difficult work. A remaining issue needs an explicit reason, impact, compensating control, owner, and next action.
- No unresolved Critical or High findings in the release scope. If a fix cannot be completed, report the audit as incomplete or constrained; do not silently accept the risk. User acceptance of a documented exception must not be described as a fix.
- Automated negative tests for fixed security invariants, successful legitimate-workflow tests, and deployed verification for host/configuration changes.
- One final complete release test gate, the relevant browser suites, fresh-install and upgrade checks, and recovery verification. Record the exact source revision and artifact hashes tested.
- A final report separating verified fixes, previously fixed findings revalidated, false positives, accepted exceptions, and untested areas. Never infer “secure” from a clean scanner output.

## 2. Planning baseline and important leads

Read-only planning inspection found `/root/boronpanel` on clean `master` at `231400957bb2989ee816801375176d9ac1e293cb`, source version 1.5.0, with `/opt/boron` resolving to `/opt/boron-1.5.0`. Recheck these at execution time. The deployed artifact and checkout need independent identity verification; sharing a version string is insufficient.

The code contains FastAPI authentication and routers, a custom Unix-socket RPC protocol, a root provisioning daemon, React interfaces, service templates, shell installers, and substantial backup/restore machinery. Existing tests and historical audits are useful evidence, not proof of current coverage.

Read these first:

- `docs/ARCHITECTURE.md`, `docs/TESTING.md`, `docs/RELEASING.md`, `docs/FRESH-INSTALL-CHECKLIST.md`.
- `docs/AUDIT-FINDINGS.md`, `docs/AUDIT2-FINDINGS.md`, `docs/AUDIT3-FINDINGS.md` and their threat models where present.
- `docs/SECURITY-REVIEW-2026-08-02.md`, `docs/SECURITY-REMEDIATION-2026-08-03.md`, `docs/CHECKPOINT-security-followup-2026-08-06.md`.
- Current WordPress, namespace, phpMyAdmin, TLS, Cloudflare, incremental-backup and recovery documentation; inspect implementation when documentation conflicts.

Priority leads from planning, to be verified rather than automatically declared vulnerabilities:

| Lead | Evidence to revisit | Required disposition |
| --- | --- | --- |
| Update authenticity | `daemon/updates.py::_download_and_verify` checks a checksum; `scripts/release.sh` treats GPG signing as optional | Trace the full path and implement authenticated release verification and a safe trust-bootstrap transition if missing |
| API-to-root authority | `api/rpc.py` passes actor/role metadata; `daemon/server.py::dispatch` consumes it for audit; socket checks identify the API UID | Distinguish ordinary route authorization from containment after API compromise; design and test meaningful daemon-side restrictions |
| IMAP migration destinations | Audit A3-4 and `daemon/imapsync.py` describe revalidation without pinning; review both folder listing and migration | Prove destination/TLS behavior at connection and reconnect, then close rebinding and credential exposure paths |
| SVG uploads | Audit A3-9; `daemon/branding.py` still documents a partial regex filter | Replace unsafe acceptance with a vetted allowlist parser or safe rasterization/rejection; retain supported logo functionality |
| Historical deferred work | A3-1 quotas, A3-11 Sieve transactions, A3-2 archive verification/extraction race, A2-5 Redis socket squatting | Reproduce on current code; close or document evidence that subsequent changes fixed them |
| Historical operational actions | Trusted TLS, FTPS enforcement, log permissions, MFA, credential rotation, verified tool installation | Check the installed host; source changes alone cannot close operational findings |
| Recent privileged features | Resellers, portable imports, malware quarantine, editable OLS, IP allocation, resource limits, WordPress management and snapshot recovery | Review afresh and connect them to older trust boundaries |

No exploitation or scanner run was performed to prepare this plan. Earlier test counts do not substitute for this audit's evidence.

## 3. Execution setup and safe testing boundary — phase 0

1. Read applicable `AGENTS.md` instructions. Start an isolated audit branch/worktree from the current revision; preserve existing user changes. Record checkout/deployment commits, hashes, package versions, architecture, running units, listeners, firewall backends, actual panel ports and relevant configuration permissions. Do not assume the README's default ports match this server.
2. Establish a protected evidence directory outside Git, e.g. `/root/boron-security-audit/2026-09-20/`, mode 0700, files 0600. Store credentials, detailed exploit evidence, raw scanner reports and potentially sensitive inventories there. Put only sanitized plans, tests and summaries in the repository. Never print secrets or commit session cookies, private keys, database dumps, or browser authentication state.
3. Capture recoverable application/configuration/database state and a backup inventory before mutation. Verify an actual restore into an isolated destination. Document whether an off-host recovery copy exists; a same-disk archive is not disaster recovery. Record what remains unprotected without inventing backup infrastructure.
4. Build a disposable Ubuntu 24.04 VM or equivalent isolated full-system environment with real OLS, MariaDB, mail and systemd behavior. Containers/mocks are useful for pure application tests but cannot establish host isolation, firewall, mount, setuid or reboot behavior. No access to live service sockets, secrets, host mounts or customer data from attack fixtures. Use an isolated source copy with dummy configuration to prevent accidental live imports.
5. Create synthetic identities: two unrelated customers A/B, two resellers with separate customer sets, an administrator, an impersonating administrator, suspended/disabled/deleted identities, and scoped/expired/revoked tokens. Create fake sites, databases, mailboxes, backups, domains and canary files for each.
6. Keep a manifest of all disposable resources and original settings. Cleanup may remove only resources created for this audit; restore settings and verify service health afterward. Do not treat previously named QA accounts as disposable without checking ownership and current contents.
7. Run aggressive fuzzing, resource-exhaustion cases, malformed archives, privilege escalation proofs, crash/reboot injection, and destructive restore tests only in the isolated environment. Live checks are bounded and target-owned. Do not scan third-party IMAP/DNS/Cloudflare infrastructure or send test mail to unrelated recipients; use controlled fixtures.
8. Before live firewall/authentication/service changes, prepare tested recovery instructions and maintain an existing administrative connection. For changes that could remove access, arrange a timed revert or verified independent recovery path. Do not reset credentials or revoke every administrator session before proving a working replacement path.

Exit: known target identity, usable lab, synthetic fixtures, protected evidence, and a tested recovery route. If a full-system lab is unavailable, continue source and isolated unit work; explicitly mark host-level checks pending rather than substituting live attack tests.

## 4. Inventory and threat model — phase 1

Generate a coverage ledger using code/AST and runtime route discovery in the lab. OpenAPI alone misses WebSockets, middleware, mounted applications, legacy HTML routes, cron tasks and direct RPC entry points.

For each entry point record: method/path or operation, file/function, authentication method, allowed roles, owning resource, input schema, authorization location, privileged side effect, output/secrets, asynchronous job continuation, applicable test, and review result. Enumerate `OP_TABLE`, timer/cron scripts, service startup hooks, proxies and installer/migration actions separately. Add a CI check requiring new entry points to acquire an explicit policy and coverage disposition.

Map trust boundaries:

- Internet/browser → API; hosted website and sibling subdomain → panel origin; bearer tokens → API.
- Customer/reseller → another account or administrator; impersonation → original administrative identity.
- API UID and API-writable database/files → privileged daemon; daemon → operating system and generated service configurations.
- Customer PHP/CLI/cron/Node/Python/WordPress code → filesystem, processes, loopback services, sockets and other tenants.
- External responses, archives, plugin packages and backup repositories → privileged parsers/restorers.
- GitHub release/build environment → root self-update; local staging files → verified artifacts.

Threats must include unauthenticated attackers, malicious customers with valid local code execution, compromised WordPress sites, compromised reseller credentials, stolen sessions, malicious import content, and compromise of the API process. Full root takeover is not preventable by an application running on that root; assess prevention, blast radius and recovery without claiming otherwise. Admin-only features still need input safety and CSRF protection because they consume untrusted data.

Revalidate old audit IDs into this ledger with fresh evidence. Distinguish design limitations from exploitable defects and documentation drift. Exit: no unclassified entry points and a prioritized review queue.

## 5. Audit and fix work packages — phase 2

Work in the order below, adapting to confirmed severity. Fix and retest a reproduced Critical/High issue promptly instead of waiting for the whole discovery pass. Search for sibling occurrences of each underlying bug class.

### S01 — Authentication, sessions and browser/API boundaries

Files: `api/security.py`, `api/main.py`, authentication/twofactor/tokens/impersonation routers, `daemon/handlers_auth.py`, `daemon/totp.py`, `daemon/impersonation.py`, frontend auth/API state.

- Check password handling, enumeration, rate limiting across IPv4/IPv6 and workers/restarts, lockout DoS, TOTP replay/recovery/enrollment/reset, token expiry/scopes/revocation, session fixation and rotation, logout, password-change invalidation and disabled-user behavior.
- Verify cookie Secure/HttpOnly/SameSite/Path/Domain and host-only behavior, cookie tossing from customer subdomains, shared cookies across admin/customer ports, CSRF on login and every cookie-authenticated mutation, Origin/Referer handling, CORS, Host validation and forwarded-header trust.
- Test impersonation start/end, session expiry mid-action, stale browser role caches, tokens during suspension, and return-to-admin restrictions. Ensure UI hiding is never the authorization control.
- Assess reauthentication/MFA for credential reveal, key export, firewall/update operations and account takeover actions. Stage any MFA enforcement with enrollment, tested recovery and a documented operator migration.

Exit: unauthorized and stale/replayed credentials cannot perform protected operations, including through alternate listeners, WebSockets and legacy endpoints; valid users retain recovery and ordinary access.

### S02 — Tenant/reseller authorization and control-plane integrity

Files: every router and its called handler; `daemon/resellers.py`, `shared/models.py`, `shared/db.py`, resource ownership helpers.

- Exercise read/write/delete/list/search/export/download/cancel/retry paths as anonymous, A, B, both resellers, admin and scoped tokens. Vary path IDs, body IDs, nested IDs, case, alternate encodings, domain names and job IDs independently.
- Check ownership in the actual handler and again when delayed jobs execute; test reassignment, rename, suspension, deletion/recreation and reseller plan changes during queued work.
- Test role/owner/plan mass assignment, forged internal metadata, overbroad response fields, inaccessible objects leaking through counts/errors, DB monitor/process controls and account lifecycle actions.
- Identify every API-writable database field used by root for paths, executable selection, config, UID/GID, job dispatch or download destinations. Treat these as untrusted at the privilege boundary.
- Enforce limits atomically for concurrent creations, bulk actions, imports, restores and reseller allocations. Verify quota failure leaves no orphan resources or unexpected credentials.

Exit: a machine-readable role/resource matrix and negative tests establish isolation across all entry points and execution stages, with positive cases for legitimate delegation.

### S03 — Root RPC, commands, filesystem and native helpers

Files: `api/rpc.py`, `shared/rpc.py`, `daemon/server.py`, `daemon/safeio.py`, `daemon/procutil.py`, `daemon/sysops.py`, `daemon/mail_restore_gate.c`, all privileged call sites.

- Test socket ownership and peer checks, missing-credential failure behavior, request shape/unknown fields, frame size/nesting/timeouts, stalled clients, dispatch allowlists and bounded execution. Reject client-supplied actor/role metadata that can override trusted context.
- Produce a daemon authorization design covering tenant, reseller, administrative and internal operations. Derive resource ownership from authoritative state; constrain operation-specific parameters and root side effects independently of the HTTP layer.
- Explicitly separate defense against a route bug from containment of a compromised API UID. Checking an API-supplied role or signing it with a key available to that same API does not prove containment. Where needed, narrow privileged helpers/channels and move the relevant authorization authority beyond API-writable state. Document residual administrative capability honestly.
- Trace every subprocess, shell, SQL identifier, template and config-writing sink. Test argument/option injection, newline/config injection, malicious PATH/environment/cwd, plugin hooks, and unsafe serialization. Run tenant-controlled code and installers as the tenant, not root.
- Test traversal, symlinks, hardlinks, rename races, mount crossings, special files, stale descriptors, temporary files and cleanup paths. Use descriptor-relative/no-follow operations and ownership/type checks at use time; `realpath` followed by pathname writes is not a race fix.
- Review native helper parsing, privilege drop, fd inheritance, setuid/capability behavior, bounds and failure paths; use compiler diagnostics/sanitizers and bounded fuzz cases in the lab.

Exit: boundary regression tests prove protected canaries and other tenants remain unchanged; malformed RPC and tenant paths cannot produce unauthorized root effects or unbounded work.

### S04 — WordPress and tenant application execution

Files: WordPress/wpmanager/wpcli/staging/appinstaller modules, PHP install helper, Node/Python/Redis/Composer/Git/cron/SSH/terminal modules and routers.

- Verify install destination/domain/DB ownership, package provenance, file permissions, DB grants, wp-config handling and cleanup after partial failures.
- Test one-click login expiration, one-use atomic consumption, replay, wrong account/site, redirect validation and token disclosure in URLs, logs, referrers, caches and browser storage. Ensure tokens cannot outlive ownership/suspension changes.
- Test clone/staging/backup/restore/remove operations for cross-account paths, shared credentials, accidental production writes, database-prefix validation and preservation of unrelated installations.
- Treat WP plugins/themes/config files and lifecycle scripts as hostile tenant code. Verify WP-CLI never grants root execution or leaks operator credentials, including failure/retry paths.
- Check terminal WebSocket auth/origin, session ownership/revocation, command environment, escape sequences, shell privilege drop and namespace behavior. Check Git URL/options/hooks, deploy keys, cron injection, app environment secrets and resource limits.
- Verify Redis per-account directory/socket permissions, startup races, stale sockets, authentication/exposure, persistence paths and cross-account access. Test limits through PHP, CLI, cron and long-lived processes, including restart/reboot reconciliation.

Exit: realistic compromised-WordPress and tenant-shell scenarios remain confined; install/login/clone/restore/terminal/Redis workflows still work.

### S05 — Backup, restore, imports and recovery

Files: `daemon/backup.py`, `daemon/portable_archive.py`, `daemon/cpanel_import.py`, `daemon/snapshot_*.py`, `daemon/rclone.py`, backup/import routers and native recovery helper.

- Treat Boron/cPanel/DirectAdmin archives and remote repositories as hostile. Test traversal, absolute paths, symlinks/hardlinks, device/FIFO entries, duplicate names, case/encoding ambiguities, forged manifests, decompression bombs, excessive counts/depth and size-limit enforcement while streaming.
- Check manifest hashes and schema/version handling without treating an attacker-supplied checksum as authenticity. Verify identity remapping, destination collisions and refusal to restore privileged users, ownership, executable modes or global settings from tenant metadata.
- Review SQL import content and restore privileges: reject or safely contain dangerous global statements, file access, grants and cross-database operations. Configuration, cron and mail restore content must pass the same validation as direct edits.
- Check remote destination credentials, encryption keys, downloads/browse tokens, public exposure, retention and cleanup, and ownership of single/all-database backup selectors.
- Inject failures at staging, swap, database, DNS/Cloudflare, mailbox/routing/Sieve, journal and finalization steps. Test cancellation, concurrent restore, disk full, restart and interrupted rollback. Ensure idempotent recovery and no stale background worker can overwrite newer state.

Exit: malicious archives cannot escape staging or acquire extra privileges; failed restores preserve unrelated accounts and recover consistently; one complete cross-server synthetic account round trip succeeds.

### S06 — Outbound requests, DNS and TLS

Files: IMAPSync, imports, webhooks/notifications, Cloudflare, DNS, SSL/panel TLS, external download and proxy modules.

- Inventory every outbound HTTP, IMAP, SSH/SFTP and other connection made for user input. Test private/loopback/link-local/metadata ranges, IPv4-mapped IPv6, mixed DNS answers, redirects, URL credentials, alternate IP notation, DNS rebinding and proxy-environment effects using controlled lab endpoints.
- Bind the validated address to the actual connection and each reconnect/redirect. Preserve TLS hostname/SNI and certificate verification; connecting to a validated IP while disabling identity checks is not an acceptable SSRF fix. Keep required internal destinations fixed and narrowly scoped.
- Bound response size, time, redirects, concurrency, retries and transcript output. Prevent credentials from going to a destination after ownership/configuration changes.
- Verify DNS zone ownership, parent/subdomain selection, Cloudflare token scope, wildcard/ACME authorization, record mutation scoping, challenge cleanup and restore reconciliation. Use ACME staging for automated issuance tests.
- Verify panel/site/OLS/FTP/mail TLS identities, key permissions, renewal/reload behavior and expiry visibility. TLS configuration must survive installer, update and certificate rotation.

Exit: prohibited destinations are blocked at connection time; legitimate migrations and backups retain verified transport security; DNS/cert operations stay within authorized scope.

### S07 — Frontend, uploads and integrated admin tools

Files: frontend components and API client, branding/site templates/custom pages, filebrowser/pma/olsadmin routers and integration configurations, deployed Roundcube integration.

- Test stored/reflected/DOM XSS through domains, logs, filenames, mail subjects, scan findings, notes, search results, templates and API errors; examine raw HTML/rendering sinks, SVG/XML uploads and MIME handling.
- Use a vetted SVG allowlist with external entities/resources disabled, or a constrained rasterization/rejection policy. Test direct navigation and embedding contexts, not just image tags. Preserve harmless valid assets.
- Verify CSP/frame protection, unsafe inline execution, content type/sniffing, cache controls for secrets and download headers. Check open redirects, proxy path normalization and trusted-header stripping.
- Re-test FileBrowser direct loopback access as each tenant UID across IPv4/IPv6 and firewall reload/reboot. A hidden UI link or a proxy-only check is insufficient if the backend trusts caller headers.
- Test phpMyAdmin SSO token lifecycle/exact DB grants and direct endpoints; Roundcube session/plugin/upload behavior; OLS admin credential reveal/reset authorization, no-store responses and configuration validation/rollback. Respect that existing OLS hashes cannot be reversed.
- Exercise Evo and Paper because shared API controls can still have theme-specific rendering and stale-state bugs. Preserve visible error/recovery states and accessibility.

Exit: malicious content is inert in a real browser, backend integrations cannot bypass authentication, and administrative secrets are neither cached nor exposed cross-role.

### S08 — Host hardening, mail and availability

Files: service/cron/logrotate templates, installer, namespace/cgroup/account management, firewall/IP/WAF/fail2ban, email/spam filtering and malware modules.

- Compare installed UID/GID/group/capability/mount/process isolation with templates. Check customer-readable files/logs, `/proc` exposure, service sockets, writable root-owned execution paths, temporary directories and inherited fds. Validate systemd restrictions per service rather than optimizing a score blindly.
- Confirm protection between tenant PHP/CLI/apps and other accounts' home/temp/session/cache data, mail, backups, database sockets and credentials. Test open_basedir and namespace behavior as defense layers, not substitutes for OS permissions.
- Inspect effective IPv4/IPv6 listeners and firewall chains, including loopback rules and dedicated/shared IP bindings. Test block/unblock/bypass validation, protected-access behavior, rule order, reload/reboot persistence, Cloudflare source trust and spoofed client-IP headers.
- Check mail relay restrictions, SMTP/IMAP/POP/FTP authentication transport, mailbox isolation, DKIM keys, Sieve validation/rollback, forwarding/autoresponder abuse and migration credential handling using synthetic recipients.
- Review malware scan execution UID, traversal, signature update provenance, queue/resource caps, report ownership, quarantine permissions, content-bound actions and restore races. Use harmless EICAR/synthetic fixtures in the lab, never real malware or live customer quarantine.
- Test bounded API/job/WebSocket/upload/log/scan/backup concurrency, cancellation, process and memory/CPU/I/O limits, inode/disk exhaustion handling and queue recovery. Do not exhaust the live server to demonstrate a limit.

Exit: effective deployed protections match tested configuration; customers cannot bypass isolation through alternate runtimes or local services; administrative access and essential mail/web services survive changes.

### S09 — Secrets, audit trails, dependencies and release supply chain

Files: audit/logging/crypto/TOTP modules, secret/config templates, requirement/lock files, installers, release scripts, updater/finalizer and CI configuration if present.

- Check secrets at rest/in transit/in argv/environment/errors/logs/audit rows/browser storage. Test nested redaction with seeded canaries, protected file creation/rotation, temporary-file cleanup, key separation, encryption migration and backup exposure.
- Scan current source and Git history for leaked credentials locally with redacted output. If real exposure is confirmed, prepare targeted revocation/rotation and dependent-service updates; do not destroy logs or rewrite published history as an automatic cleanup. Preserve restricted incident evidence.
- Check audit attribution, tenant visibility, event/log injection, privileged operation coverage, failure recording, retention and tamper resistance. Document that root can modify local logs; consider an external sink only if available/authorized.
- Inventory Python/npm/OS/PHP/third-party binaries and actual deployed versions. Run pinned, recorded versions of dependency/secret/static-analysis tools; triage reachability and deployment context. Check Ubuntu package revisions/backports against Ubuntu Security Notices, not upstream version strings alone; consult vendor advisories and CISA KEV prioritization.
- Audit acquisition of WP-CLI, imapsync, Composer, FileBrowser, runtimes, malware signatures and backup tools. Verify immutable version/digest/signature and repository trust, safe failure, and absence of unsigned fallback. Keep dependency/build/install hooks away from root where practical.
- Require publisher authentication for releases, protecting the trusted key or identity policy independently of the downloaded archive/checksum. Verify before extraction, dependency install, migrations or privileged code. Test invalid/missing signatures, wrong key, revoked/rotated key, wrong version/repo, corrupted archives and malicious staging replacement.
- Close verify/reopen races using protected immutable staging or the same verified open object. Enforce anti-downgrade for normal updates while preserving an explicit, authenticated recovery rollback with compatible data migrations.
- Design signing migration before enforcement: current clients need a trusted public key/identity provisioned through an authenticated path. A key downloaded beside an unsigned artifact cannot bootstrap trust. Keep signing private keys out of the panel host where feasible. An unavailable trust prerequisite must remain visible, not trigger a silent checksum-only fallback.
- Test fresh install and upgrade from v1.5.0, mixed-version restart, finalizer interruption, schema compatibility, health checks and rollback. Verify that release tooling gates the exact shipped source/artifact, including any version changes made after tests.

Exit: confidential material stays scoped, logs support investigation, reachable dependency vulnerabilities are addressed, and root updates cannot accept an unauthenticated or substituted artifact.

## 6. Findings, remediation and evidence rules

Use IDs `BSA-2026-001`, etc. Each finding records severity and confidence separately, CWE and applicable ASVS/WSTG mapping, affected revisions/deployments, attacker prerequisites, minimal safe reproduction, observed versus expected result, impact/blast radius, root cause, sibling search, patch reference, negative and positive tests, deployment action, rollback and retest evidence.

Prioritize root execution/privilege escalation, unauthenticated takeover, cross-tenant write/read and update trust, followed by account takeover, secrets, injection/SSRF and significant availability issues. Explain contextual severity; a scanner's rating alone is not the panel's rating.

Allowed states: suspected, confirmed, fixing, fixed-in-source, lab-verified, deployed-verified, not-reproducible-with-evidence, false-positive-with-evidence, or explicitly accepted exception. Keep code fixes and live remediation status separate. Preserve old IDs by linking them; do not overwrite historical reports as though they described today's deployment.

For each fix: reproduce safely → encode a failing invariant test → fix the shared cause → test nearby variants and legitimate behavior → review migration/compatibility → record evidence. For policy-only improvements without an exploit, test the required behavior without inventing a vulnerability proof. Prefer small coherent commits; avoid unrelated visual or feature refactors.

## 7. Efficient verification — phase 3

Use existing `scripts/test.sh` tiers. Inspect test isolation before running security cases as root; mocks that accidentally miss a filesystem/service call must not touch live `/etc`, sockets or data.

| Stage | Verification | When |
| --- | --- | --- |
| Initial baseline | Targeted auth/isolation/RPC/safe-I/O/updater cases; inventory existing failures | Once at start, in isolation |
| Individual fix | Exact regression plus affected module/integration tests | Every changed security invariant |
| Work-package checkpoint | `scripts/test.sh quick` plus related browser/host integration cases | After a coherent group settles |
| Final candidate | Complete suite through the required release gate, frontend production build and relevant Playwright suites in both themes | Once after fixes stabilize; repeat only for new changes/failures requiring it |
| Artifact acceptance | Signature/integrity/rejection tests, fresh install, v1.5.0 upgrade, restart and recovery in a full-system lab | Exact release candidate |
| Deployed acceptance | Safe auth/role/isolation checks, changed host controls, ordinary hosting workflows, health/version/artifact identity | After deployment |

Do not perform a manual full run and then unnecessarily repeat it through the release script. Arrange one mandatory final full gate without weakening the release script. If publishing edits source/version, verify the shipped revision and rerun affected checks; earlier success never authorizes changed security code automatically.

Use Bandit/Semgrep or equivalent for Python patterns, ShellCheck for shell, pip-audit/npm audit and an OS/binary inventory for dependencies, a local redacting secret scanner, and an authenticated web scanner such as ZAP in the lab. Tool selection is subordinate to coverage; avoid redundant scans. Record tool versions, rule sets, advisory timestamps and failures. No network/advisory access means “not checked,” not “zero vulnerabilities.”

Add targeted property/fuzz tests for path/archive validators, RPC framing, identifiers, SVG/XML parsing and URL validation where they expose real input space. Time-box individual fuzz runs, not unresolved findings. Run tests concurrently only when they have independent ports, databases, filesystem roots and service state. Mocked tests are insufficient for isolation, TLS, kernel or root-helper claims.

## 8. Release and deployment — phase 4

1. Review the coverage ledger and finding states; all Critical/High issues must meet the stated exit criteria. Recheck prior fixes after shared helper changes.
2. Prepare sanitized security release notes, exact package manifests/hashes, required configuration/secret migrations and operator recovery instructions. Keep detailed exploit evidence private until fixes are available; do not publish raw reports with credentials or tenant details.
3. Build and verify the signed candidate with mandatory tests. Exercise it in the lab through fresh install and the real self-update path from the deployed baseline. Include rejected malicious/unsigned artifacts and interrupted updates.
4. Apply within existing user authorization for release/deployment; if that authorization does not cover this new release, finish the reviewable candidate and ask only for the remaining publication/deployment decision. Do not repeatedly request permission for routine authorized fixes/tests.
5. Take and verify the immediate pre-change recovery point, apply migrations/host fixes through reproducible installer/updater/reconciliation code, and deploy through the panel's update mechanism. A live-only permission or firewall edit is not a durable fix.
6. Verify exact artifact identity, service state, admin/customer/reseller login, WordPress one-click login and clone/backup on synthetic sites, mail/DB/Redis/terminal/FileBrowser/phpMyAdmin/OLS access, certificate validity, firewall persistence and core tenant isolation. Inspect logs without exposing secrets. Verify database integrity and before/after resource inventories.
7. Test rollback in the lab and confirm the installed rollback path. Some secret/schema/authenticity migrations require forward recovery; document these before release and do not restore compromised credentials or silently re-enable a known vulnerable release.
8. Remove only audit-created fixtures and credentials, preserve protected evidence, and provide the final report plus operational monitoring/follow-up actions.

## 9. Deliverables and Sol handoff

Sol should maintain:

- `docs/SECURITY-AUDIT-COVERAGE-2026-09-20.md` (and CSV/JSON if useful): entry points, trust boundaries, requirement mappings, review/test evidence and exclusions.
- Protected findings ledger and evidence under `/root/boron-security-audit/2026-09-20/`; sanitized `docs/SECURITY-AUDIT-REPORT-2026-09-20.md` when appropriate for the repository.
- Root-cause fixes, adversarial regression tests, required install/upgrade migrations and concise checkpoint notes.
- Release validation record tying commands/results to source/artifact identity; recovery and credential-rotation instructions where needed.
- A final user-facing result: findings by severity, what was fixed and deployed, tests passed/failed, operational changes, remaining limitations and exact release version.

Execution instruction for GPT Sol:

> Execute this plan against the current BoronPanel checkout and deployment. Start with phase 0, then inventory and review in risk order. Preserve customer data and existing Evo/Paper functionality. Fix confirmed issues and their sibling cases with regression evidence. Use focused tests while iterating and the complete gate for the final release. Do not inherit historical “fixed” claims without retesting, do not defer security work because it exceeds an arbitrary time budget, and do not report unfinished or untested work as complete. Keep a resumable checkpoint identifying the current commit, finding states, completed checks, pending work and next action. Follow the user's current execution/release authorization.

## 10. External verification references

Checked while preparing the plan; Sol must refresh advisory/version data during execution.

- [OWASP ASVS official repository and 5.0 resources](https://github.com/OWASP/ASVS) — requirement baseline and versioned requirement identifiers.
- [OWASP WSTG project](https://owasp.org/projects/web-security-testing-guide) and [version 4.2](https://wstg.owasp.org/v4.2/) — repeatable web-testing procedures; the project identifies 4.2 as its current published version.
- [Ubuntu Security Notices](https://ubuntu.com/security/notices) — Ubuntu-specific affected/fixed package revisions and OVAL references.
- [CISA Known Exploited Vulnerabilities catalog](https://www.cisa.gov/known-exploited-vulnerabilities-catalog) — exploitation-informed prioritization. Direct retrieval returned 403 during planning; no claim is made that the current deployed stack was checked against it.

This plan specifies a comprehensive review scope. It does not claim a completed security audit, zero vulnerabilities, or independent third-party assurance.
