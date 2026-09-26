# Mail, DNS, resource isolation, security and stack-management plan

Status: **implemented as the v3.0.0 release candidate; final release verification is in progress** (2026-09-26).

This plan covers the requested webmail, database, DNS, branding, backup-mailbox, resource-control, filesystem-isolation, database-governor, firewall/WAF, OpenLiteSpeed and server-stack work. It is a large platform change. The implementation is divided into independently verifiable work packages, with high-risk enforcement introduced in monitor or canary mode before it becomes a server default.

The existing backup/security/hosting plan remains the source of truth for work already completed or in progress: [PLAN-backups-security-hosting-2026-09-25.md](./PLAN-backups-security-hosting-2026-09-25.md). This document extends it and does not reopen its completed UI fixes or replace its recovery contracts.

## 1. Decisions and principles

1. **Ubuntu-native implementation.** Boron will implement CloudLinux-inspired controls using Linux cgroup v2, systemd, OpenLiteSpeed (OLS) namespace containers and MariaDB Community features. The interface will not claim to be CloudLinux, CageFS, LVE or MySQL Governor. Those names describe licensed products and kernel-integrated behavior that stock Ubuntu cannot reproduce exactly.
2. **Local DNS remains the default for new installations.** Existing servers and existing zones retain their current Local, Cloudflare or DNS Cluster assignment until an administrator explicitly changes them. No migration will silently move zones or overwrite records.
3. **Secure defaults with staged enforcement.** DNS/mail correctness and UI fixes can become defaults. Filesystem isolation, database enforcement and changed resource limits begin in observe/canary mode on upgraded servers. Fresh installations can enable certified defaults after validation.
4. **One authority for each system.** Existing Boron certificate, DNS, firewall, WAF, queue and OLS transaction paths will be extended. The work must not introduce a second firewall manager, a second DNS source of truth or ad hoc root shell calls from the API.
5. **No arbitrary package or command execution.** Stack Manager uses a Boron-pinned support catalog, signed package repositories and typed jobs. Users cannot supply package names, repository URLs, shell arguments or service commands.
6. **Theme consistency is an acceptance requirement.** New pages use Evo and Paper components, typography and grid rules. Equal icon canvases, the custom-logo first paint, the dashboard spacing fixes and responsive layouts must not regress.

## 2. Current-state findings

- Server setup and configuration already default to local DNS. PowerDNS is installed with a local backend, while Cloudflare and DNS Cluster are supported as alternative or downstream providers.
- New local zones currently receive SOA/NS, apex A and `www` records. Mail and FTP records are added through other flows, so a newly added domain does not begin with a complete, understandable template.
- Boron can publish SPF, DKIM and DMARC records when provisioning mail, but the current code does not guarantee that an outbound DKIM signing service is installed and active. Publishing a DKIM public key without signing outgoing messages must be fixed before the panel represents DKIM as healthy.
- Service certificate handling already knows the configured webmail hostname and can deploy its certificate into OLS. The missing work is a reliable, visible SSL workflow with DNS/HTTP preflight, issuance state, renewal state and end-to-end SNI verification.
- The current Webmail action only opens the Roundcube URL. Mailbox secrets are stored as password hashes, so Boron cannot and must not recover the user's existing password for automatic login.
- The database customer page already supports most underlying actions: database and user creation, grants, password reset, rename, check/repair, import and phpMyAdmin. Its information architecture and action hierarchy are the main problems.
- Single-mailbox backup currently accepts an item-reference string. The required domain/mailbox selectors can use the existing mail inventory while the backend continues to validate ownership.
- Account compute limits already use systemd/cgroup v2 for CPU, memory, tasks and I/O bandwidth. CPU is stored as a percentage where 100% equals one logical core, even though newer screens describe cores. OLS PHP workers are periodically moved into an account slice, leaving a possible short interval before enforcement.
- The reseller CPU field combines `min=0.01`, `step=0.25` and a default of `0.5`; that step grid makes valid-looking values fail browser validation.
- Node.js, Python and Redis already run as account services. They need consistent cgroup assignment and stronger systemd sandboxing.
- Firewall and WAF foundations are already substantial: UFW rules, protected services, trusted/bypass addresses, temporary changes with automatic rollback, ModSecurity and OWASP CRS modes. The requested work should simplify their interface and improve OLS-layer controls while preserving recovery behavior.
- OLS vhosts already support rewrite rules and configuration transactions. A bounded `.htaccess` watcher and manual reload action can use that path.

## 3. Research basis

The resource vocabulary and expected effects are based on [CloudLinux limits](https://docs.cloudlinux.com/cloudlinuxos/limits/) and [LVE Manager](https://docs.cloudlinux.com/cloudlinuxos/lve_manager/). Linux enforcement is based on [cgroup v2](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html) and systemd resource-control primitives. VMEM will not be added because CloudLinux itself treats it as deprecated.

Filesystem isolation will build on [OpenLiteSpeed Namespace Containers](https://docs.openlitespeed.org/config/advanced/namespaces/), which provide per-user mount namespaces and an OLS-supported execution path. OLS security and runtime work follows its official [per-client throttling](https://docs.openlitespeed.org/security/throttling/), [rewrite and `.htaccess` behavior](https://docs.openlitespeed.org/config/rewriterules/), [ModSecurity integration](https://docs.openlitespeed.org/modules/modsecurity/), [SSL configuration](https://docs.openlitespeed.org/security/ssl/) and [PHP external-app guidance](https://docs.openlitespeed.org/config/php/externalapp/).

The one-click webmail design uses Roundcube's documented [plugin hooks](https://github.com/roundcube/roundcubemail/wiki/Plugin-Hooks) and the short-lived alternate-credential pattern used by [DirectAdmin webmail login](https://docs.directadmin.com/other-hosting-services/webmail/). It does not place a master password in Roundcube or expose a reusable mailbox password to the browser.

The database-governor design uses MariaDB's native [account resource limits](https://mariadb.com/docs/server/reference/sql-statements/account-management-sql-statements/grant) and [USER_STATISTICS](https://mariadb.com/docs/server/reference/system-tables/information-schema/information-schema-tables/information-schema-user_statistics-table). These provide measurable, enforceable controls, but do not provide the same kernel-level per-query CPU/I/O throttling as CloudLinux MySQL Governor.

Version policy will follow upstream support data rather than hard-coded claims in the UI: [PHP supported versions](https://www.php.net/supported-versions.php) and [MariaDB release information](https://mariadb.org/about/). Brand assets will come from the official [Node.js](https://nodejs.org/en/about/branding), [Python](https://www.python.org/community/logos/), [Git](https://git-scm.com/community/logos/) and [Redis](https://redis.io/wp-content/uploads/2024/09/Redis_BrandGuidelines_Partners_Vol01_20240903_SM.pdf) sources and will be stored locally with attribution/licensing notes.

## 4. Work packages

### M1 — Webmail hostname SSL

Add a clear **Service Certificates** section to the existing SSL page for Panel, Webmail and phpMyAdmin. The Webmail card shows hostname, DNS result, active certificate, issuer, expiry, renewal status and OLS deployment status, with **Issue/Renew certificate** and **View diagnostics** actions.

Issuance must:

1. Validate the hostname and ensure it is the configured webmail name.
2. Resolve A/AAAA records and verify that the expected server address is reachable. A mismatched AAAA record must be reported rather than ignored.
3. Confirm that the ACME challenge path is served by the intended OLS vhost.
4. Queue issuance through the existing privileged certificate job, never through a frontend shell call.
5. Deploy the key/certificate using an atomic OLS config transaction, validate the configuration, gracefully reload OLS and verify the certificate through an external-style SNI connection.
6. Enable HTTP-to-HTTPS redirect only after HTTPS is valid. Preserve a usable diagnostic state if issuance fails.
7. Register renewal and alert before expiry. Keep private keys unreadable by the API/frontend and redact ACME output.

The first implementation targets the one configured server webmail hostname. Per-customer `webmail.example.com` aliases require additional per-domain certificate and OLS alias lifecycle and are a separate decision in section 8.

Acceptance: a fresh server can issue and renew the configured webmail certificate from the SSL screen; Roundcube loads over HTTPS without a name warning; failed DNS, IPv6 or ACME checks produce an actionable state without breaking the previous certificate.

### M2 — Secure one-click Roundcube login

Add **Open Webmail** beside each mailbox. The design uses a one-time launch exchange because Boron stores mailbox passwords only as secure hashes.

Flow:

1. A direct customer session sends `POST /api/v1/accounts/{account}/email/{mailbox}/webmail-session` with CSRF protection. API tokens cannot use it. The root boundary verifies the authenticated account, mailbox ownership, mailbox/domain/account state and rate limit.
2. Boron creates a cryptographically random, opaque launch token. Only its hash is stored. It expires in at most 60 seconds and can be redeemed once.
3. The browser submits the token by POST to the configured Roundcube origin in a new tab. It is never placed in a query string, referrer, local storage or application log.
4. A Boron-maintained Roundcube plugin redeems it over a narrow root-owned Unix socket. Peer UID and request schema are verified.
5. Redemption creates a separate short-lived IMAP/SMTP credential for that mailbox. Dovecot accepts it through an additional scoped passdb; SMTP submission continues through Dovecot SASL. The ordinary mailbox password remains valid and undisclosed.
6. The session credential expires with a bounded Roundcube session, and is revoked on logout, mailbox password change/delete, domain/account suspension and explicit administrator revocation.
7. Audit records contain actor, mailbox, IP, creation, redemption and revocation outcome, but never token or credential material. Responses use `Cache-Control: no-store` and a strict referrer policy.

Administrator impersonation will not silently create a mailbox session. A separate audited permission could be added later; the safe default is to require the customer to sign in directly.

Acceptance: valid one-click login reaches the selected inbox; token replay, expiry, CSRF, cross-account substitution, disabled mailboxes and impersonated sessions fail; concurrent mailbox sessions can be revoked independently; no secret appears in URL/history/logs.

### M3 — Mail readiness, default DNS records and DMARC controls

Create a provider-independent mail DNS reconciler used by Local DNS, Cloudflare and managed cluster zones. It reports each record as present, missing, conflicting, invalid or unmanaged and produces a preview before modifying existing zones.

For a new Boron-managed zone, the local default template will contain:

- SOA and authoritative NS records.
- Apex A and optional AAAA, plus `www` CNAME.
- `mail` address/alias according to the selected mail-host architecture.
- MX pointing to a hostname with a valid mail-service certificate.
- SPF TXT with a single valid policy.
- DKIM TXT only after a real signing key and signing service are ready.
- `ftp` address/alias.

DMARC remains an explicit Email DNS action with **Monitor (`p=none`)**, **Quarantine** and **Reject** presets, aggregate-report address, subdomain policy and a rendered record preview. The panel must warn before enforcement modes and must not create duplicate SPF/DMARC records.

Install and configure an actual maintained DKIM signing path, preferably Rspamd's DKIM signer when the mail/security stack uses Rspamd, otherwise OpenDKIM. Connect it to Postfix through a local milter, enforce key permissions, rotate keys safely and verify an outbound message contains a valid signature before showing DKIM as healthy.

Existing zones receive a **Review and repair mail records** workflow. It will never silently replace externally managed MX/SPF/DKIM/DMARC records. Cluster changes are batched so one logical repair produces one final sync operation rather than a race of partial updates.

When no mailbox exists, mail-dependent pages show: **No email accounts yet. Create an email account to use forwarding, spam controls, webmail and mailbox backups.** Domain setup problems remain available under diagnostics instead of appearing as the primary empty state.

Acceptance: new local zones receive the agreed template idempotently; Cloudflare/cluster modes write only managed zones; outbound mail passes SPF and DKIM verification; DMARC presets validate; conflicts are previewed and preserved until approved.

### U1 — Official product marks and stable icon rendering

Add repository-owned SVG assets for Redis, Node.js, Python and Git from their official brand sources. One shared brand-icon component will normalize the viewBox, visible canvas, padding, color behavior and accessible label. It will be used in Evo, Paper, search results and feature pages.

Do not fetch brand assets at runtime. Record source and license information. Retain the common tile dimensions and grid behavior that fixed uneven Evo/Paper layouts, and verify that the logo loaded in the initial HTML/theme bootstrap is the configured Boron logo so the old default logo cannot flash first.

Acceptance: the four official marks are recognizable and equal in visual weight across both themes/light modes; no icon changes tile size or dashboard spacing; refresh and slow-load recording show no default-logo flash.

### U2 — Database page information architecture

Rebuild the customer database page around three top-level views:

- **Databases:** name, size, user count, tables and status, with one primary **Manage** action.
- **Users:** database users, associated databases and a clear create-user action.
- **Access & privileges:** attach/detach users, presets and custom privileges using owned database/user dropdowns.

The Manage view/drawer groups Overview, phpMyAdmin, Import/Export, Users & Privileges and Maintenance. Reset password, check tables, engine-appropriate repair and rename remain available, while destructive or uncommon actions move to a labeled overflow menu with confirmation. The page reuses existing APIs unless a missing ownership/status field requires an additive response.

Acceptance: common create/open/attach operations are visible without scanning a dense action row; all prior functions remain reachable; cross-account names cannot be submitted; Evo/Paper and mobile layouts have no clipped action menus.

### B1 — Guided single-mailbox backup

When backup type is **Single mailbox**, replace free text with two dependent selectors:

1. Hosted mail domain.
2. Mailbox, displayed and submitted as the full address such as `billing@example.com`.

Changing the type or domain clears stale mailbox state. Include loading, empty and error states, search for accounts with many mailboxes, and a link to create a mailbox when none exist. The backend must resolve the selected mailbox from owned inventory and reject a forged address even if the frontend is bypassed.

Acceptance: users cannot accidentally back up a mailbox from another domain/account; the queued item and recovery catalog consistently show the full address; deleted mailbox selections fail before capture begins.

### R1 — Resource policy model and plan editor

Create a versioned account resource policy with inheritance: server defaults → reseller plan/account plan → account override. Preserve existing CPU/memory/I/O/PID fields through a compatibility adapter and migration. Every effective value records its source and its last successful application state.

Supported limits:

| UI term | Linux/OLS mechanism | User-visible effect |
| --- | --- | --- |
| CPU cores | cgroup `CPUQuota` (`1 core = 100%`) and optional weight | Sustained CPU is throttled after the quota is consumed. |
| Memory | cgroup `MemoryHigh`/`MemoryMax` with explicit policy | Pressure/throttling first where configured; hard limit may terminate processes. |
| Disk I/O | cgroup `IOReadBandwidthMax`/`IOWriteBandwidthMax` | Read/write throughput is throttled. |
| IOPS | cgroup `IOReadIOPSMax`/`IOWriteIOPSMax` | Operation rate is throttled. |
| NPROC | systemd/cgroup `TasksMax` | New processes/threads are refused at the limit. |
| Entry processes | OLS LSAPI max connections/request concurrency | Concurrent dynamic requests queue or receive a bounded busy response. |

CPU fields use preset choices `0.25`, `0.5`, `1`, `2`, `4`, `8`, **Unlimited**, plus a validated Custom option. Decimal validation must align its minimum and step. RAM, EP, NPROC, I/O and IOPS use units and recommended presets instead of raw unexplained numbers. The full-page plan editor separates Compute, Memory, Disk/I/O, Web concurrency and Hosting quotas, with effective-limit preview.

Account PHP workers must enter the correct slice at spawn time where OLS permits, rather than relying only on periodic relocation. Reconciliation detects escaped processes and configuration drift. Node.js, Python and Redis services remain directly assigned to the account slice.

Acceptance: limits apply to plans, reseller-created accounts and direct account overrides; changing a plan reconciles affected accounts safely; a controlled workload demonstrates CPU, memory, I/O, IOPS, EP and NPROC behavior and the UI describes the observed result accurately.

### R2 — Resource Manager and limit history

Add **Resource Manager** under Admin → Server Management with Users, Plans, History and Faults views. Show current usage, effective limits, source plan, throttling/limit events, drift and last apply result. Provide reset-to-plan, temporary override with expiry and Unlimited where safe.

Store bounded time-series samples and discrete limit-hit events. Aggregate older samples to control database growth. Display familiar average/limit/fault indicators inspired by LVE Manager while clearly labeling Boron's native implementation. Alerts can use the notification framework after it exists.

Acceptance: administrators can identify a constrained account, see which limit was hit, trace the effective policy and safely change/revert it. Normal usage sampling does not overload the daemon or database.

### I1 — Filesystem and process isolation

Use OLS Namespace Containers for PHP/web requests with a Boron-owned minimal namespace template:

- System binaries/libraries needed by hosted applications are read-only.
- Only the account's own home/docroots are writable.
- Other `/home` content is absent.
- Temporary directories are private.
- Required DNS, CA, mail and database sockets/configuration are exposed narrowly.
- Host executable passthrough uses an explicit allowlist.

Use OLS `cmd_ns` (or the documented equivalent for the installed supported version) for the customer terminal so it sees the same filesystem boundary and cgroup policy. Harden Node.js, Python and Redis systemd units with `NoNewPrivileges`, capability removal, private temp and explicit read/write paths. Design SSH/SFTP coverage separately; do not label the feature fully isolated while an enabled entry point still exposes other users.

Ubuntu 24's AppArmor restrictions on unprivileged namespaces must stay enabled. Validate OLS version/capabilities and use the supported privileged OLS namespace path rather than weakening the server globally.

Add **Filesystem Isolation** to Server Management with per-account coverage (Web/PHP, Terminal, Node/Python/Redis, SSH/SFTP), status, exceptions, canary enable, rebuild/unmount and a diagnostic self-test.

Rollout: audit-only compatibility scan → disposable-server tests → selected canary account → opt-in accounts → new-account default. Upgraded servers do not switch all customers in one operation.

Acceptance: a user can use WordPress, WP-CLI, Composer, mail sending, ImageMagick and its databases while being unable to read another account's home, credentials or processes. Namespace teardown/rebuild and OLS restart leave no stale mount or inaccessible account.

### DB1 — Database Monitor and Boron DB Governor

Merge governor functions into **DB Monitor** with tabs: Overview, Live Queries, Users, Governor Policies and Events. Enable MariaDB `userstat` with a measured overhead check and collect per-user connections, CPU/busy time, rows, bytes and statement duration.

Policies may set:

- `MAX_USER_CONNECTIONS`.
- `MAX_QUERIES_PER_HOUR`.
- `MAX_UPDATES_PER_HOUR`.
- `MAX_CONNECTIONS_PER_HOUR`.
- `MAX_STATEMENT_TIME` where the installed certified MariaDB version supports it.
- Rolling warning/restriction thresholds and cooldown.
- An optional, tightly bounded action to terminate selected long-running statements.

Start in **Monitor only**. Enforced policies are opt-in per plan/account and use hysteresis/cooldown to prevent flapping. Protect Boron, system, backup/restore and migration users from customer policy assignment. Record every automatic action and provide a global pause switch.

The panel must state the limitation: native MariaDB controls can reject/limit connections and statements reactively, but cannot provide the same smooth per-query CPU and disk-I/O throttling as CloudLinux MySQL Governor.

Acceptance: one database user cannot affect another account's policy; monitor mode never kills/restricts; enforcement applies and later releases a test account; WordPress, backup, restore and migration functions continue to work; administrator pause takes effect immediately.

### S1 — Simplified Firewall and WAF experience

Create a **Security Center** overview while preserving the existing UFW and ModSecurity engines.

Firewall views:

- **Status & presets:** Recommended Hosting, Strict Hosting and Custom, with a plain-language preview.
- **Services & ports:** service name first, advanced protocol/address fields on demand.
- **Trusted IPs:** permanent or expiring bypass with reason; suggest the current management IP but never trust it automatically.
- **Blocks:** manual and automated blocks, expiry and source.
- **Advanced & recovery:** raw managed rules, pending-change timer, export/import and console recovery instructions.

WAF views:

- **Protection:** Disabled, Detect and Protect with current CRS/version status.
- **Domain overrides:** searchable, with inherited state.
- **Exceptions:** narrow rule/URI/parameter exceptions with expiry.
- **Events:** domain, verified client IP, rule, result and one-click scoped exception workflow.

Keep the existing preview → protected-rule validation → independent rollback timer → apply → fresh-connectivity confirmation sequence. Changes must not strand SSH/panel access if the browser disconnects or the worker dies.

Acceptance: a new administrator can choose a safe preset without understanding raw UFW syntax; advanced controls remain available; protected management rules and automatic rollback cannot be bypassed through the simplified API.

### S2 — OLS-layer abuse and DDoS controls

Expose OLS per-client controls as **Balanced**, **Strict** and **Custom** presets:

- Static and dynamic requests per second.
- Inbound/outbound bandwidth.
- Connection soft/hard limits, grace period and ban duration.
- Keep-alive/request timeout bounds.
- Trusted proxies/IPs and WordPress login/XML-RPC protections.

When Cloudflare proxying is enabled, trust visitor headers only from the current verified Cloudflare networks and preserve a safe update/fallback path. Feed temporary application-layer blocks into the existing firewall/security-event model without creating hidden permanent rules.

The UI must describe the boundary: these controls reduce application/connection abuse but cannot absorb a volumetric attack that saturates the server's network link. Cloudflare or upstream provider filtering is required for that case.

Acceptance: representative bursts are throttled according to the preset; legitimate static pages and normal WordPress administration remain usable; trusted-proxy handling cannot be forged by a direct client; disabling the feature restores the prior valid OLS config.

### O1 — Reliable `.htaccess` reload

Ensure every managed OLS vhost enables rewrite and `autoLoadHtaccess 1`. Add a root-side inotify watcher over registered document roots that:

- Watches `.htaccess` create/write/delete and atomic rename patterns, including relevant nested directories.
- Resolves the affected owned vhost without following a symlink outside its allowed tree.
- Debounces/coalesces event storms.
- Validates ownership/path and the generated OLS configuration.
- Requests one graceful OLS reload through the existing config transaction/lock.
- Records success/error and applies rate limits/backoff.

Add **Reload `.htaccess`** to customer site tools and the admin domain action menu as a manual fallback. A bad rule may break that site's request, but must not replace the previous valid server configuration or take all vhosts down.

Acceptance: changes made by File Manager, SFTP, terminal and atomic editor saves become effective quickly; nested rules work; a write storm produces bounded reloads; unrelated sites remain available.

### ST1 — Setup Wizard Stack step and Stack Manager

Add an admin-only Stack step to fresh setup and a separate **Stack Manager** page for existing servers. It detects installed version, package source, support status, assigned usage and compatible actions for OLS, PHP and MariaDB.

**OpenLiteSpeed**

- Install/update only from the pinned, signed official package source supported by Boron.
- Simulate package operations first, back up Boron/OLS configuration, validate disk space and dependencies, drain/reload safely, run configuration and HTTP health checks and restore the prior config/package state where rollback is supported.

**PHP**

- Offer only the PHP versions and extension sets certified by the current Boron release.
- Use the upstream support feed/catalog to label Active, Security fixes, Legacy and EOL.
- New installations cannot select EOL versions. An already installed legacy version remains visible with a warning until all assigned sites migrate.
- Prevent removal of the server default or a version assigned to a domain; provide assignment and compatibility preview.

**MariaDB**

- Ubuntu 24's MariaDB 10.11 remains the conservative default until additional majors pass Boron's full compatibility matrix.
- Fresh installations may select only versions explicitly certified in that Boron release. Never offer an unbounded **latest** choice.
- Minor security updates run as durable maintenance jobs with preflight and health checks.
- A major upgrade requires verified full database backup, free-space check, supported upgrade path/package origin, maintenance window, explicit irreversible confirmation, `mariadb-upgrade`, application/Boron health checks and a documented restore path. No in-place downgrade is offered.

All stack actions use durable jobs with phases, progress, cancellation boundaries and audit entries. The frontend sends typed selections from the signed catalog, never command strings.

Acceptance: a fresh disposable Ubuntu 24 server can install a certified stack; a failed simulated/update health check does not leave OLS or MariaDB half-configured; assigned PHP versions cannot be removed; major MariaDB upgrade testing includes a restore drill before any production availability.

## 5. Admin navigation

Add or consolidate these tiles under **Server Management** without crowding the grid:

- Resource Manager.
- Filesystem Isolation.
- DB Monitor (including Governor).
- Stack Manager.
- OpenLiteSpeed.
- Server Setup.

The category uses the same fixed column tracks as other Evo sections so item count does not change tile width. Paper uses its established equal-size icon grid. If the category exceeds a readable size, move closely related existing tools into a second clearly named row/section rather than shrinking icons or restoring the previous catch-all layout.

## 6. Data, API and job boundaries

1. Add versioned schema objects for resource policies/effective state, resource samples/faults, webmail launch/session credentials, DB governor policies/events and stack catalog/job state. Use migrations with backward-compatible reads during upgrade.
2. Root-side methods accept typed, length-bounded identifiers and selections. They re-resolve account, domain, mailbox and database ownership; frontend data is never authority.
3. Every external mutation is a durable job or a short atomic transaction with a visible final state. DNS, OLS, firewall, certificates and package operations use their existing subsystem lock/reconcile paths.
4. Secrets are encrypted/redacted at rest where applicable and absent from job arguments, process listings, URLs and logs. Webmail one-time secrets are hash-only.
5. Audit security-sensitive creation, policy changes, enforcement, certificate deployment, firewall/WAF changes and stack changes with actor and result.
6. Feature flags/operating modes allow isolation, governor enforcement and new OLS throttles to be disabled independently during rollout.

## 7. Implementation and verification sequence

### Phase 0 — Contracts and fixtures

- Freeze API/schema contracts, threat boundaries, migration compatibility and feature flags.
- Add representative fixtures: empty mail domain, many mailboxes, conflicting DNS, existing custom SPF/MX, database with multiple users, legacy plans and resource-heavy accounts.
- Record a UI baseline for Evo/Paper, light/dark, admin/customer and responsive widths so earlier spacing/icon fixes cannot regress.

### Phase 1 — Low-risk UX and correctness

- U1 official icons/logo first paint.
- U2 database organization.
- B1 mailbox backup selectors.
- M3 empty states and DNS status/preview, without enabling DKIM healthy state until signing is installed.

### Phase 2 — Mail and DNS service integrity

- Complete real DKIM signing and the default/reconciliation templates.
- Complete M1 webmail SSL.
- Complete M2 one-click login after a focused threat review.

### Phase 3 — Resource controls

- R1 schema, compatibility adapter and full plan editor.
- Enforce each cgroup/OLS control on a disposable server.
- R2 sampling/history and administrator UI.

### Phase 4 — Isolation and database governance

- I1 compatibility audit, namespace template and terminal/service integration.
- Canary one disposable/test account before any upgraded-server default.
- DB1 starts monitor-only; enable enforcement only for explicit test policies.

### Phase 5 — Security and OLS behavior

- S1 simplified Security Center while retaining current recovery mechanics.
- S2 OLS throttling presets and proxy correctness.
- O1 `.htaccess` watcher/manual reload.

### Phase 6 — Stack lifecycle

- ST1 detection/catalog and fresh-install flow first.
- PHP add/remove/assignment safety.
- Minor upgrades.
- Major MariaDB upgrades only after snapshot/disposable-VM restore drills.

### Phase 7 — Integrated release candidate

- Upgrade a copy of an existing Boron server and install a fresh Ubuntu 24 server.
- Run migration, rollback, restart and reconciliation checks.
- Complete browser review, documentation, release notes and operator recovery guide.
- Release after the combined verification gates. The user authorized the major release on 2026-09-26.

## 8. Test strategy

Development should stay fast by testing each package at its boundary instead of running the full suite after every edit.

- **Per change:** affected unit/contract tests, lint/type checks for touched code, and a targeted UI component/browser scenario.
- **At phase boundaries:** frontend production build, affected backend integration suite and targeted Playwright across Evo/Paper.
- **Once for the combined release candidate:** full backend/frontend suite, installer/upgrade test, both themes/light-dark/admin-customer/responsive visual pass, daemon restart/recovery and security regression suite.

Mandatory high-value tests:

- Webmail: CSRF, ownership, replay, expiration, concurrency, revocation, impersonation denial, URL/log leakage and IMAP/SMTP login.
- DNS/mail: idempotency, existing-record conflict, local/Cloudflare/cluster routing, DKIM live signing/verification, malformed SPF/DMARC and partial provider failure.
- Resources: CPU, memory, I/O, IOPS, EP and NPROC workloads; service restart; escaped-process reconciliation; plan inheritance and Unlimited.
- Isolation: attempts to read another home, `/proc` data and secrets; symlink/mount escape attempts; WordPress/Composer/WP-CLI/mail/database/terminal/Node/Python/Redis compatibility.
- DB Governor: monitor-only invariants, cross-account isolation, threshold/cooldown, long-query handling, global pause and backup/restore exclusions.
- Firewall/WAF: bad management-port rule auto-rollback, worker/browser loss, fresh SSH/panel connection, false-positive exception and detect/protect behavior.
- OLS: throttling with real client identity behind/direct from Cloudflare, `.htaccess` write/rename/storm, invalid rule isolation and graceful reload.
- Stack: signed source validation, simulated failures, interrupted jobs, OLS health rollback, PHP assigned-version guard, MariaDB backup/upgrade/restore.

Destructive enforcement and stack-upgrade tests run only on disposable VMs or snapshots, never on a live customer source server. External provider claims are not marked passed without real credentials and an end-to-end result.

## 9. Rollout defaults

For a fresh certified installation:

- Local DNS is selected by default.
- Complete non-conflicting DNS/mail template is enabled.
- Webmail SSL and renewal are enabled after DNS verification.
- Resource limits follow the selected plan.
- WAF begins in Detect mode before Protect is chosen.
- DB Governor begins in Monitor only.
- Filesystem isolation can be the new-account default only after the compatibility matrix passes.

For an upgraded server:

- Existing DNS provider/zone ownership and records stay unchanged.
- Existing resource values are translated without reducing quotas unexpectedly.
- Namespace isolation and DB Governor enforcement remain off until canary approval.
- Existing firewall/WAF rules remain effective while the UI is reorganized.
- Stack upgrades never run merely because a newer version is available.

## 10. Implemented decisions and delivery record

The approved implementation uses the Ubuntu-native controls in this plan. It does not label them CloudLinux, CageFS, LVE or MySQL Governor. The first webmail release secures the configured server webmail hostname. Mail clients use one canonical TLS hostname and MX target.

| Package | Delivered behavior | Verification state |
|---|---|---|
| M1 service TLS | Panel, webmail, phpMyAdmin and mail service cards; dedicated Postfix/Dovecot certificate paths; atomic certificate validation, deployment and rollback | Unit/integration coverage for service discovery, separated mail paths and failed reload rollback |
| M2 webmail SSO | Direct-customer-session-only one-time Roundcube launch, hash-only launch tokens, scoped temporary Dovecot credentials, UDS peer validation, replay/expiry/revocation and redacted audit events | Root authorization, API origin/session, token lifecycle and Evo/Paper browser POST-handoff tests |
| M3 mail DNS | Local-zone MX, SPF, mail and FTP defaults; provider-neutral preview/repair; explicit DMARC presets; OpenDKIM keys, tables, Postfix milter and status | Conflict/idempotency tests and signer/config tests; live outbound signature delivery still requires a configured public test domain |
| U1/U2/B1 | Official product marks, Server Manager placement, reorganized database workflow and guided domain/full-mailbox backup selectors | Production build plus Evo/Paper light/dark and responsive browser matrices |
| R1/R2 | CPU cores/weight, memory pressure/hard limit, read/write throughput, IOPS, NPROC and entry-process policies with inheritance, temporary overrides, sampling, faults and reconciliation | Policy inheritance, cgroup translation, expiry retry, retention-boundary and UI tests |
| I1 isolation | OLS web/PHP namespace configuration, per-account inspection/rebuild/self-test, and stronger systemd hardening for Node.js, Python and Redis | Configuration and UI coverage; existing SSH and web terminal remain bounded by Unix account permissions and cgroups and are reported separately |
| DB1 governor | MariaDB native account limits, USER_STATISTICS monitoring, monitor/enforce/pause modes, events and new-database-user reconciliation | Native-limit, failure rollback, startup/new-user reconciliation and DB Monitor UI tests |
| S1/S2/O1 | Consolidated Security Center, WAF modes, safe OLS abuse-control presets, trusted-proxy-aware real IP template, manual reload and coalesced `.htaccess` watcher | OLS transaction, watcher retry/coalescing, theme and responsive UI tests |
| ST1 stack | Typed OpenLiteSpeed, side-by-side LSPHP and MariaDB 10.11 inventory/preview/install/update jobs, package simulation, serialized execution, config backup and post-change validation | Job contract/unit tests and Stack Manager browser tests |

The deliberately staged defaults remain: DB Governor begins in Monitor, filesystem isolation is visible and canaried before wider enforcement, WAF begins in Detect, and an upgraded server keeps its existing DNS/provider assignments. OLS workers may take one reconciliation interval to enter their account cgroup. Stack jobs preserve configuration and stop on failed validation; package downgrades and cross-series MariaDB rollback require an operator snapshot or disposable-server migration workflow.

The repository-level security diff review covered every changed runtime file and reported no high or critical findings. Its medium database-governor lifecycle finding and low temporary-policy expiry finding were corrected before the release candidate; dedicated mail-certificate separation and DKIM/SPF ownership defects found during architecture review were also corrected.
