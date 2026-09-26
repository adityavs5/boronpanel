# Backup, security, DNS and hosting improvements

Status: implementation complete; combined release validation in progress (2026-09-26).

The user approved implementation, testing, and one coordinated major release after this plan was written. The work packages below are now the implementation record and acceptance contract. External AWS, Backblaze, and Google Drive credentials and an isolated second-host firewall target were not supplied; those real-provider and lockout trials remain explicit post-release acceptance dependencies and are not represented as passed.

## 1. Findings that determine the implementation

- The current installer configures PowerDNS with its SQLite backend and local REST API. `daemon/dnsprovider.py` routes active Cloudflare zones to Cloudflare; other zones use local PowerDNS, and local changes can enqueue DNS-cluster updates. Public authority still depends on registrar/parent-zone delegation. Merely running PowerDNS does not make this server authoritative for every hosted domain.
- Two backup implementations already exist: account archives and encrypted restic snapshots. Snapshot destinations already include local, SSH and S3, including AWS/Backblaze provider identifiers. Jobs, retention and several component restore operations exist. The work must unify and extend these paths without losing existing backups or presenting a scheduled/failed job as a usable backup.
- `DatabaseGrant` currently associates a unique database name with one user. Independent users and many-to-many grants require a schema change affecting WordPress, phpMyAdmin, migration, cleanup and backups as well as the database screen.
- `daemon/wpcli.py` already builds WP-CLI plugin/theme listing commands. The WordPress dialog parses job stdout and automatically repeats a listing if the result is not an array. That is a concrete possible loop path, not yet a runtime-confirmed diagnosis. The old installer module's comment about not using WP-CLI does not describe all current management operations.
- Root-side SSH-key and 2FA policies explicitly reject impersonated sessions. The reported messages match those branches. Direct customer sessions must be checked separately; the fix must not allow an impersonating administrator to silently replace a customer's 2FA.
- The terminal deliberately starts Bash with profiles disabled. The ASCII banner is sent only when `identity.role == "admin"`, explaining why customers can miss it.
- OLS vhost templates specify per-domain access/error log paths, `WARN` error level, 10 MB rolling size and 30-day access-log retention. The existing customer OLS viewer reads only the error file. An empty error log is not proof that access logging is broken.
- Existing UFW protection/bypass rules and ModSecurity/OWASP CRS integration are foundations to extend. OLS WAF handling is server-wide with domain-conditioned rules, so per-domain controls need validation against the actual installed engine.
- The two uncommitted files, `frontend/src/themes.css` and `frontend/src/components/themes/ToolDashboard.jsx`, contain the last spacing/loading change. Preserve them. Their final visual verification is still pending and must not be represented as passed.

## 2. Research and design references

The proposed account-first backup flow follows JetBackup's account selection and component recovery workflows. Archived means an uncompressed account archive; compressed means a compressed account archive; incremental snapshots retain independent recovery points while reusing data. These are separate storage modes, not alternate names for a full rescan. Sources: [JetBackup Accounts](https://docs.jetbackup.com/v5.4/adminpanel/accounts.html), [Backup Jobs](https://docs.jetbackup.com/v5.4/adminpanel/backupJobs.html), [Restore & Download](https://docs.jetbackup.com/v5.4/adminpanel/restoreAndDownload.html).

Destination controls and notification integrations are informed by [JetBackup Destinations](https://docs.jetbackup.com/v5.4/adminpanel/Destinations/destination.html) and [Notification Plugins](https://docs.jetbackup.com/v5.4/adminpanel/Plugins/notificationplugins.html). Boron's equivalent to “Export JB Config” will be called **Export Backup Configuration**, containing Boron configuration and recovery metadata.

Database workflows are based on [cPanel Manage My Databases](https://docs.cpanel.net/cpanel/databases/manage-my-databases/) and [DirectAdmin MariaDB/MySQL](https://docs.directadmin.com/other-hosting-services/mariadb-mysql/). The concrete deliverables below specify which operations will be implemented.

Storage references: [restic repositories](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html), [rclone SFTP](https://rclone.org/sftp/), [rclone Drive](https://rclone.org/drive/), [Google Drive scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth), [Drive resumable uploads](https://developers.google.com/workspace/drive/api/guides/manage-uploads), [Drive limits](https://developers.google.com/workspace/drive/api/guides/limits), [AWS multipart integrity](https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity-upload.html), [Backblaze S3 integration](https://www.backblaze.com/docs/en/cloud-storage-get-started-with-a-backblaze-integration).

Security references: [Ubuntu UFW](https://manpages.ubuntu.com/manpages/noble/man8/ufw.8.html), [OLS ModSecurity](https://docs.openlitespeed.org/modules/modsecurity/), [OWASP CRS](https://owasp.github.io/www-project-modsecurity-core-rule-set/), [Imunify360 interface](https://docs.imunify360.com/dashboard/). These inform the management workflow; implementation uses the existing open-source engines and Boron controls.

Other references: [Cloudflare delegation](https://developers.cloudflare.com/dns/zone-setups/full-setup/setup/), [Cloudflare proxy ports](https://developers.cloudflare.com/fundamentals/reference/network-ports/), [WP-CLI plugin listing](https://developer.wordpress.org/cli/commands/plugin/list/), [OLS log configuration](https://github.com/litespeedtech/openlitespeed/blob/master/dist/docs/AdminGeneral_Help.html).

The supplied subdomain image was downloaded and visually reviewed: [user reference](https://rahul.node01.n8n.sitecountry.com/f.php?h=3vOyyYN9&p=1), local research copy `/tmp/boron-subdomain-reference`. It shows the editable subdomain label immediately followed by the parent domain, then document-root choices. The prior Evo/Paper references remain in `/root/ui-references/`. JetBackup documentation was readable through web research; direct screenshot-page download returned HTTP 403, so no claim is made that JetBackup screen captures were obtained in this pass.

## 3. Work packages and implementation order

### U0 — Preserve the UI baseline and establish shared screen patterns

1. Preserve the horizontal custom logo, no-default-logo flash behavior, real WordPress/Redis artwork, equal icon canvas sizes, readable theme typography and working dashboard search.
2. Keep independent Evo and Paper styles. Backup pages use familiar tables, date selectors, tabs, rows and clear action bars fitted to each theme; no crowded catch-all dashboard groups or oversized decorative cards.
3. Make dashboard groups flow consecutively in the left column. All groups share the same column tracks at each breakpoint: six on wide desktop, fewer consistently at tablet/mobile widths. Do not use item-count-based auto-fit that creates different button widths per group.
4. Replace dependence on matching two fixed column heights with a bounded desktop resource rail: when metrics are taller than the tools, the rail scrolls within the available viewport instead of forcing a blank region below the tools. On small screens the rail follows the tools in normal flow. Do not stretch cards or insert filler icons to balance height.
5. Make new categories work with stored collapse preferences, route permissions and fuzzy-search aliases. Update obsolete tests that hard-code the previous category counts while preserving alignment assertions.
6. Later visual review must scroll the actual panel content container; a viewport-only screenshot mislabeled as full-page is insufficient.

Completion: admin/customer, Evo/Paper, light/dark, loading/loaded/failed metrics, expanded/collapsed groups, refresh, theme switching and narrow screens remain usable, with no horizontal overflow, clipped actions or recurring inter-section/bottom-left blank region.

### B1 — Account-first Backup Manager and recovery catalog

Admin landing page: **Accounts**, with a searchable, paginated account table showing username, primary domain, latest usable recovery point, recovery-point count, covered components, destinations, last job result, freshness and last verification time. Distinguish “No backups”, “Not scheduled”, “Overdue”, “Destination unavailable”, “Partial” and “Available”. Show backed-up former/deleted accounts in a separate filter for disaster recovery; do not silently assign them to a new account with the same username.

Selecting an account opens its recovery page, newest backups first, with date/time, mode, destination, available components and verification status. Offer full-account and selected-component restore/download, with files/folders, databases, database users/grants, email, DNS and account configuration selectors. Preserve existing supported configuration recovery, including cron/PHP/mail settings. The chosen date and any missing components are explicit; never silently mix dates.

Customers open their own recovery page directly and never see other accounts or storage secrets. Queue, downloads and activity remain visible after leaving/reopening the page or refreshing. Show per-account and per-destination failures accurately.

Implementation: a durable catalog over existing archive/snapshot data; versioned manifests with server/repository/account identity and component inventory; read existing history without rewriting backup contents. Separate last successful backup from latest job attempt. Remote checks run as queued jobs rather than on every page load. “Available” is based on completed cataloged data plus verification age, not a promise of permanent remote availability.

Completion: an administrator can identify accounts without recovery points, open any account, select an older usable point and queue a restore/download. Existing archives and snapshots remain accessible.

### B2 — Incremental, compressed and archived backup modes

| Destination | Incremental | Compressed archive | Uncompressed archive |
| --- | --- | --- | --- |
| Local storage | Yes | Yes | Yes |
| SSH storage | Yes | Yes | Yes |
| SFTP-only storage | Yes | Yes | Yes |
| Amazon S3 | Yes | Yes | Yes |
| Backblaze B2 through S3 | Yes | Yes | Yes |
| Other existing S3-compatible providers | Yes | Yes | Yes |
| Google Drive | **No** | Yes | Yes |

- Retain restic for encrypted incremental/deduplicated snapshots. Explain that SQL data is exported consistently and then deduplicated; this is not MySQL binlog point-in-time recovery. A forced full read is a rescan, not an archive mode.
- Build independent portable `.tar.gz` and `.tar` account exports with checksums and a versioned manifest. Encryption is a separate, explicit setting using a maintained authenticated-encryption implementation, with recovery-key export; do not invent cryptography. Preserve encryption requirements for secrets and repository data.
- Share the capture/restore inventory across modes: home files, databases/users/grants, mail and mail configuration, domains/subdomains/document roots, SSL, DNS, FTP, cron, PHP and supported app configuration. Missing/unsupported components must fail or be visibly excluded before execution, never be called a successful full backup.
- Inventory/source capture and per-account locks prevent conflicting restores, account deletion and overlapping jobs. Use consistent SQL exports, record file-change warnings, and offer a coordinated maintenance/quiesce option when an application-consistent file/database point is required.
- Check staging space, free-space reserve, permissions, CPU/I/O budgets and estimated size before starting. Upload into temporary objects, verify, then publish the completed manifest. Resume/retry safely; interrupted transfers do not become restore points.
- Restore stages and validates data before applying it, creates an accessible pre-restore recovery copy, reports partial failure, and preserves the existing safe mail/database/file restore protections. Full account reconstruction is a coordinated worker, not a raw extraction into `/home`.
- Downloads are asynchronous and account-authorized, with readable names, size/checksum, expiry and private staging cleanup. Support generating a portable archive from incremental data. Large downloads are bounded/streamed rather than loaded into API memory.

Completion: every supported destination/mode combination has a future real backup → download → restore round trip, including changes, deletions, empty items and interrupted transfers. Drive incremental jobs are rejected by the backend as well as disabled in the UI.

### B3 — Destination setup and authentication

- Provider chooser: Local, SSH, SFTP, Amazon S3, Backblaze B2, S3 Compatible, Google Drive. Retain previously supported S3 providers.
- SSH and SFTP offer **Username + password** or **SSH key**. Key mode supports generated or supplied dedicated keys and encrypted-key passphrases. Host, port, username, root path and host-key fingerprint are separate fields. Require confirmed host-key pinning and reject unexpected changes; credentials never appear in process arguments, history, browser storage or logs.
- Implement SFTP without a shell/rsync dependency on the destination. Use a constrained storage transport, with restic's documented rclone backend where needed for incremental SFTP/password authentication. Store transport configuration privately and disable ambient agents/configuration.
- AWS and B2 get provider-specific help, defaults and field names, bucket/prefix selection, region/endpoint validation and scoped credentials. Validate multipart upload/download/delete capabilities and end-to-end checksums; S3 ETags alone are not universal file checksums.
- Google Drive uses an administrator-configured OAuth client, browser authorization, destination folder selection and private refresh-token storage. Do not depend on a public/shared rclone client. Support reconnect/revocation and resumable archive uploads with rate-limit backoff and bounded concurrency. Incremental is unavailable for Drive.
- Destination forms expose only relevant fields, show an explicit connection/capability result and retain entered non-secret values after failures.

Completion: both SSH authentication methods, shell-free SFTP, provider presets and Drive reconnect work; incorrect credentials/host keys and unsupported mode combinations produce actionable errors without leaking secrets.

### B4 — Destination operations, visibility and configuration recovery

Provide these actions with queue progress and recorded results:

| Action | Required behavior |
| --- | --- |
| Test destination | Connectivity, credentials, permissions and a small read/write/delete check restricted to Boron's own probe path; read-only destinations receive a read-only check. |
| Speed test | Explicit bounded upload/download using disposable probe objects, elapsed time and throughput; cancel and clean up only those objects. |
| Reindex | Discover valid Boron manifests/snapshots, rebuild catalog entries, identify missing/corrupt data, and require mapping for foreign/deleted accounts. Never initialize, overwrite or prune a repository as part of reindex. |
| Browse destination | Browse only the configured root/prefix; distinguish raw storage objects from logical restorable files in encrypted snapshots. Customer browsing stays limited to owned recovery points. |
| Enable/disable | Stop new backup writes to a disabled target without hiding failures or destroying existing recovery data; existing read/restore availability is explicit. |
| Backup visibility | Admin-only/customer-visible setting enforced in API listings, browsing, downloads and restore, including direct URLs. |
| Delete destination | Dependency/active-job checks; remove the configuration without automatically deleting remote backups. Destructive storage purge is a separate explicit action. |
| Export Backup Configuration | Encrypted export of destination definitions, jobs, retention, notifications, repository namespaces, catalog recovery data and required secrets, protected by an independently saved recovery key. |
| Import configuration | Dry-run preview, validation, reconnect and reindex on a replacement server; no remote backup overwrite or automatic execution of imported jobs. |

Automatic configuration exports accompany scheduled protection. Keep retention distinct from storage-provider lifecycle rules; uncoordinated object expiry must not remove chunks still needed by snapshots. Reindex/retention/restore share repository locks.

Completion: losing the local catalog is recoverable from configuration plus destination contents; a missing backup is marked accurately; disabling or deleting configuration cannot erase remote history accidentally.

### B5 — Jobs, schedules, queue and dashboard navigation

Full-page job editor: General → Accounts → Components/exclusions → Mode → Destinations → Schedule/retention → Performance → Notifications → Review. Use account/database/destination dropdowns, searchable selection, sensible defaults and advanced sections.

Support all/selected/excluded accounts, manual/hourly/daily/weekly/monthly schedules with time zone, daily/weekly/monthly retention tiers, on-demand retention, pinned recovery points, missed-job/freshness alerts and separate pre-restore recovery retention. Multiple destinations report independent transfer results. “Run now”, edit, duplicate, pause/resume scheduling, safe cancel and retry failed work are explicit. Cancellation is offered only where the worker can stop safely; restore application must finish or recover its active step.

Persist queue state, phases, bytes/files where measurable, warnings, timestamps and logs across refresh/daemon restart. Avoid invented percentage estimates. Retention cannot delete the last usable point or data in use by another restore/download; protected failed-restore recovery points require explicit resolution.

Create a dedicated **Backups** dashboard category at the bottom for admin and customer in both themes, superseding the earlier instruction to merge backups elsewhere. Remove misplaced duplicate backup tiles while retaining existing URLs through redirects.

- Admin six tiles: Backup Manager, Backup Jobs, Destinations, Restore & Downloads, Queue & Logs, Notification Plugins.
- Customer six tiles: Backup Manager, File Backups, Database Backups, Email Backups, Full Account Backups, Downloads & Activity.

Component tiles deep-link to the same recovery manager. Configuration/DNS/cron/PHP recovery remains available through its component selector. Export/import configuration is in destination/settings actions, not a misleading customer permission.

Completion: all six tiles lead to functional pages, sections use uniform tracks, jobs survive restart, restore/download progress stays visible and backup menus do not clutter other categories.

### B6 — Backup Notification Plugins

Build a dedicated plugins page for Email, Telegram and Webhook integrations: configure, enable/disable, explicitly send a test, event selection and delivery history. Provide job-level recipients/channels, failures/partial completion/success/overdue/destination unavailable/restore and download events, digest frequency and deduplication.

Store tokens/secrets privately; use signed webhooks with retries/backoff, bounded payloads and outbound-request protections. Telegram uses a configured bot/chat. Differentiate queued, provider accepted and failed delivery; do not report mail receipt merely from SMTP acceptance. Customers can configure permitted own-account preferences; global tokens/destinations remain admin-only. No messages are sent as part of this planning phase.

Completion: events route only to configured recipients, repeated worker callbacks do not spam, delivery errors are inspectable and secrets stay redacted.

### S1 — Recoverable server firewall, after the backup chapter

Extend existing UFW integration; do not introduce a second competing firewall manager. Provide overview, inbound/outbound port rules, IP/CIDR allow/block/bypass, IPv4/IPv6, service presets, expiring bans, comments, search, recent changes and export/import of versioned configuration. Default outbound access remains usable for DNS, package updates, backups and APIs; advanced restrictions display affected services.

Changes follow preview → validate → persist old state and independent rollback timer → apply → confirm fresh connectivity → commit. Default confirmation window: 120 seconds. Timer recovery must not depend on the Boron API, browser or a surviving provisioner worker. A disconnected browser, failed apply or daemon crash restores the last known-good rules; reboot recovery handles an uncommitted change before it can become permanent.

Detect actual SSH/panel ports and listening addresses, rather than assuming port 22. Preserve loopback, established connections, necessary ICMP/IPv6 functions, management access and explicit bypass ranges. Existing connections alone cannot prove new connections are possible: confirmation uses a fresh connection and later lab testing must probe from a second host. Ban sources and Fail2ban honor protected/bypass addresses. Trust Cloudflare headers only from verified proxies; never ban all visitors by treating a proxy IP as an end-user IP.

Include a documented local-console recovery command for reverting the most recent pending change and selectively disabling Boron-managed filtering. A recoverability design cannot guarantee access during a provider outage or OS failure; it must prevent the panel's own changes from permanently locking out management.

Completion: deliberately wrong rules, wrong ports, process termination and absent confirmation roll back in an isolated network environment; legitimate custom-port SSH/panel access works through fresh connections.

### S2 — Configurable WAF and security incidents

Use installed OLS ModSecurity with maintained OWASP CRS and Boron controls. Provide Disabled, Detect only and Protect modes; start new rules in detection mode. Expose global/per-domain policies, rule ID/category, targeted URI/parameter exclusions, threshold/paranoia controls, timed exceptions and rule-update rollback.

Build an incident list with time, domain, verified client IP, rule, reason and action; inspect an event, add a narrowly scoped exception, unblock an IP, and return to protection. Include rate/brute-force controls for common WordPress login/XML-RPC abuse with configurable limits and conservative defaults. Coordinate temporary bans with S1 rather than creating hidden permanent firewall state.

Validate OLS configuration before reload. Domain overrides must respect actual virtual-host/alias ownership and not let a forged Host header disable protection on unrelated sites. Preserve ACME, panel management and expected WordPress editing/upload/REST/WooCommerce workflows. Keep malware-file scanning separate from network/WAF decisions, while linking related activity in the security UI.

Completion: harmless requests stay usable, representative attacks are blocked in Protect mode, detection-only traffic passes with recorded incidents, and a false positive is reversible without disabling all protection.

### H1 — Per-domain suspend/unsuspend

Add an explicit domain state/reason and actions in customer domain management plus admin views. Customer suspension pauses only the selected website using the configured suspension page; keep files, DNS, databases and mail. Show related aliases affected by the same vhost and handle subdomains explicitly. Invalidate LiteSpeed serving/cache paths so the state is visible immediately.

Account-level/admin-enforced suspension takes precedence. A customer can reverse their own domain suspension but cannot undo an administrator/account restriction. Persist state through OLS rebuilds, backups, restore and migration.

Completion: the selected site pauses/resumes while sibling sites and mail remain intact; effective state survives restart and is explained in the UI.

### H2 — Subdomain form matching the supplied reference

Use one joined input: `[ blog ] [ .example.com ▼ ]`. Parent selection immediately follows the label, with a full resulting hostname preview. At narrow widths preserve reading order and association without horizontal overflow.

Below it keep Boron's actual document-root choices and resolved path preview: separate website folder by default, existing parent root, or a chosen account-relative folder. Do not copy DirectAdmin filesystem paths into Boron. Select only owned parent domains, validate labels/duplicates, prevent docroot path escapes and create the record in the correct provider/parent DNS zone.

Completion: users can understand the final hostname before submitting, and the chosen path, vhost, PHP and DNS record agree after creation.

### DB1 — Independent database users and complete grant management

Build sections for Databases, Database Users, Add User to Database, Privileges and Remote Access. Keep a create-database-and-user wizard as the easy default.

Implement independent user create/delete/password reset/rename; one user on multiple databases and multiple users per database; assign/revoke through dropdowns; full hosted-database access, read-only and custom privilege presets; current-user/privilege display; database size/table count, SQL export/import and phpMyAdmin entry. Add database rename as a guarded copy/validate/switch job with a recovery copy and explicit application-configuration impact; do not imply an unsupported native RENAME DATABASE exists. User removal must not drop databases.

Add check and engine-appropriate repair controls. Explain unsupported repair operations for InnoDB instead of presenting a false universal repair button. Remote host grants require an allowed exact host/IP policy and any required firewall approval; they do not automatically expose port 3306 to the world.

Migrate `DatabaseGrant` to separately owned databases, users and grants, with backward-compatible read adapters. Preserve existing SQL names, hashes, privileges and WordPress connections. Integrate the new relationships into backups/restores, DirectAdmin/cPanel import, account deletion, password management and phpMyAdmin. Reject cross-account grants and system/global privileges at the root boundary.

Dependency: design the versioned database-user/grant backup schema during B1/B2, before writing new manifests. DB1's UI lands later, and final backup acceptance is repeated against the completed many-to-many model.

Completion: two users with different privileges can share a database; one user can access two owned databases; revoke affects only the chosen grant; legacy WordPress and migration/restore still work.

### DNS1 — Explicit DNS operating mode and migration workflow

Add a separate **DNS** administration section containing DNS Setup, Zones, Cloudflare, Cluster, Nameservers and Diagnostics. Customer DNS tools stay scoped to owned domains and the selected provider.

The setup choices are Fully Cloudflare DNS, Local PowerDNS and DNS Cluster. The selected server mode is the default for new zones; existing zones retain their effective provider until an explicit migration finishes. In Cloudflare mode do not silently fall back to publishing public records in local DNS if the token fails. Local PowerDNS can remain private staging/recovery infrastructure while Cloudflare is the public authority.

Cloudflare mode validates scoped token access, available zones and exact parent-zone matching, then clearly distinguishes pending delegation from active service. Local mode configures authoritative listening, nameservers/glue requirements, TCP/UDP 53 and delegation diagnostics. Cluster mode builds on the existing implementation with authenticated peers, zone ownership/direction, serial/conflict control, retries, health and convergence reporting; avoid mutual overwrite loops.

Changing mode previews record differences, exports current zones, stages the destination, handles DNSSEC/DS/CAA and external delegation steps, verifies authoritative answers and switches effective writes only when ready. Preserve current service while delegation propagates. Never announce completion solely because local API writes succeeded, and do not claim to change registrar settings without a registrar integration.

Completion: account/domain/mail/ACME operations use the correct authority, apex/subdomain zone selection works, failed provider/cluster requests are visible and recoverable, and existing sites continue resolving throughout a staged move.

### SETUP1 — Resumable server setup wizard

Expose the same persistent wizard during initial setup and under Server Setup for existing installations. CLI installation can save an incomplete draft and print the HTTPS completion URL; cloud credentials stay out of shell history/command arguments.

Steps: (1) hostname/IP/admin contact; (2) DNS operating mode; (3) provider credentials/nameservers/cluster peers; (4) service hostnames and proposed webmail/phpMyAdmin/panel records; (5) resolve/delegation verification; (6) hostname/panel/service SSL issuance and renewal; (7) optional MaxMind account/license/database setup or explicit Skip; (8) review and finish.

DNS credentials precede automatic records, and DNS verification precedes HTTP-based SSL issuance. Offer a supported DNS-01 route when available. Make records on unsupported Cloudflare proxy ports, including the current panel port 2222, DNS-only; detect port choices rather than blindly enabling the orange cloud. Avoid overwriting unrelated zone records and display record ownership/conflicts.

Provide retry/resume, step logs, “waiting for external DNS” states, idempotent re-entry and future step registration. Preserve prior settings on partial failure and let an existing administrator rerun one section safely.

Completion: all three modes work through fresh setup and reconfiguration, webmail/phpMyAdmin/hostname certificates use the selected names, propagation delay is resumable, and skipping MaxMind does not block completion.

### WP1 — Plugin-list loop and WordPress responsiveness

Trace plugin/theme query → command job → stdout → JSON result and stop the dialog's unconditional auto-retry-on-non-array loop. Define a structured extension-list response; separate process diagnostics/PHP warnings from JSON data; terminate polling on completed/failed/cancelled states and deduplicate in-flight listings.

Use existing WP-CLI as the account user with the site's mapped PHP runtime. For inventory, skip unnecessary remote update checks and ordinary plugin/theme execution where compatible. Handle must-use plugins/drop-ins and bootstrap failures explicitly; never silently disable a customer's extensions just to list them. Cache a successful list briefly per account/site, refresh on requested actions and manual reload, and check update availability separately.

Completion: an ordinary reference site aims for a warm list within one second and a cold local list within three seconds; measure these later rather than promise them for arbitrary broken sites. A failing/slow site has a bounded timeout and one actionable error, never an endless spinner/job loop. Update/activate/deactivate invalidates the relevant list once.

### AUTH1 — SSH Keys session/UI correction

Distinguish direct customer, API token, admin-selected account and admin impersonation. Correct identity/credential forwarding for legitimate direct customer sessions if broken. Customers can list/add/remove only their own authorized public keys, with fingerprints and action results.

For impersonated views, replace the crashing control with a clear customer-login requirement. If an explicit admin account-key management workflow is desired later, implement it as a separately authorized/audited operation; do not silently weaken the customer-only route now.

Completion: direct customer SSH key management works, unsupported sessions show an intentional state, and no cross-account/ephemeral-terminal-key access is introduced.

### AUTH2 — Customer 2FA correction

Fix direct-customer status/setup/verify/recovery-code/disable flows and ensure each call carries the actual session user's identity. Keep admin and customer settings scoped to their own panel users. An impersonated session explains that the customer must sign in directly to manage 2FA; it cannot enroll, disable or read another user's secrets.

Completion: enrollment plus a fresh login challenge works, invalid/reused codes fail correctly, recovery codes are shown safely and used once, password-confirmed disable works, and supported customer screens never show a raw “current panel session required” crash.

### TERM1 — Terminal welcome and prompt

Render the configured ASCII welcome (default Boron) for customer sessions as well as eligible admin views, once per successful connection. Use the existing validated plain-text renderer. Provide a normal `username@host:directory` prompt through a controlled interactive-shell environment while retaining the intended suppression of the old OS/server-info MOTD.

Completion: banner precedes a usable prompt after connect/reconnect; no duplicate banners, shell-code interpolation, leaked server details or broken input/resize behavior.

### LOG1 — Domain web logs, levels and 90-day retention

Separate access/error/PHP logs clearly. Verify actual vhost paths, writer permissions and reader authorization; expose “no entries yet” differently from missing file, inaccessible file and disabled logging. Show domain selector, severity/date filters, bounded live tail, rotated-history listing and authorized downloads.

Default web access/error retention: **90 days**, rolling at **50 MiB** or daily, whichever occurs first; gzip closed segments. Retain by age rather than a fixed count of 90 files, because busy sites can rotate many times daily. Choose one coordinated rotation mechanism per log: prefer validated native OLS rotation where supported, with a controlled compression/age cleanup job for closed segments. Do not have OLS and logrotate race over the same active file or rely on lossy copytruncate.

Admin controls per-domain error level and rotation/retention through OLS settings; customer read-only viewing is sufficient for the initial delivery. Debug logging is time-limited and automatically returns to its prior level. Low-space alerts expose actual retention risk; do not silently delete younger logs while claiming 90 days. Preserve existing PHP logging and tenant path/symlink protections.

Completion: known test requests/errors appear in the correct files, rotated archives remain readable, compression does not lose writes, and age-based cleanup retains all segments in the 90-day window within provisioned storage.

### Q1 — Deferred validation, integration and major release

No tests run during planning. Once authorized, use focused checks per meaningful batch and one combined release gate, not repeated full suites after every small edit. If the user keeps the test hold during implementation, record checks as deferred and leave high-impact firewall/DNS/auth/database changes off the live server until validation is authorized.

Validation matrix:

1. Backup round trips for every B2 combination, both SSH auth modes, SFTP-only server, actual AWS/B2/Drive test prefixes when credentials are available, corrupt/incomplete objects, reindex from a lost catalog, retention locks, disconnect/retry/cancel and missing-key cases. Provider mocks/emulators are not recorded as real-provider acceptance.
2. Full-account and component restore, download integrity/expiry, legacy archives, deleted accounts, database users/grants, mail/DNS/SSL/app metadata, fresh-server configuration recovery and neighboring-tenant isolation. Disposable accounts only.
3. Firewall/WAF isolated rollback/crash/reboot/IPv6/custom-SSH-port trials, second-host fresh connections, false-positive exceptions and WordPress normal/attack fixtures. No dangerous deny-all trial on the primary panel.
4. DNS/wizard provider migrations, delegation wait/retry, cluster convergence, record preservation, hostname SSL renewal and external-token failure.
5. Database many-to-many grants, old accounts, password changes, rename recovery, supported checks/repairs, remote hosts and DirectAdmin/cPanel import compatibility.
6. WP cold/warm/error performance, direct/impersonated SSH/2FA sessions, terminal startup and 90-day log rotation using controlled clocks/fixtures.
7. Browser matrix: both themes, both roles, light/dark, 320/390/768/1024/1440/1920 widths, slow responses, large/empty account inventories, long labels, keyboard focus and actual content-container screenshots. Custom-logo first visit/cache/reload/error, shared grid tracks/icon sizes, backup section placement and fuzzy search are explicit release blockers.

A recoverability/feature is complete only with recorded implementation and appropriate evidence; provider credentials/isolated host access are future acceptance dependencies, not reasons to mislabel skipped work as passed. After all approved checks: preserve config/data and rollback artifacts, commit the batches, publish one signed coordinated release when authorized, exercise the panel self-update, then inspect the actual live build as admin and direct customer. No live source-customer migrations/restores are authorized by this plan.

## 4. Delivery batches and dependencies

| Batch | Packages | Gate before next chapter |
| --- | --- | --- |
| 0. Baseline/design contracts | U0; B1 manifest/ownership contract; DB1 compatible schema design | Approved page map, role matrix, storage capability matrix and preserved pending UI diff. |
| 1. Backup product | B1–B6 | Complete account flow, formats, destinations, jobs, restore/download and notifications; unresolved provider validation clearly tracked. |
| 2. Firewall and WAF | S1–S2 | Independent recovery works in an isolated environment; per-domain protection can be diagnosed and reversed. |
| 3. Hosting controls | H1–H2, DB1 | Domain lifecycle, joined subdomain form, many-to-many SQL grants and backup compatibility. |
| 4. DNS and setup | DNS1, SETUP1 | Clear authority, safe mode transition and resumable service DNS/SSL setup. |
| 5. Operational fixes | WP1, AUTH1–AUTH2, TERM1, LOG1 | Each reported fault has a resolved supported path and a deliberate unsupported-session state where applicable. |
| 6. Combined acceptance and release | Q1 and final U0 review | User authorizes tests/release; all required evidence complete before publication and live self-update. |

Schema/contract work needed by backups is designed first even when its standalone UI ships in a later batch. Work is considered an implementation batch, not a separate public release. No parallel agents are required for this plan.

## 5. Implementation and validation record

The approved work landed as reviewable commits from `a854a23` through the final release candidate. The implementation includes the account-first recovery catalog, legacy archive visibility, portable archive modes and encryption, SSH/SFTP/S3/Drive destination contracts, queued destination operations, configuration recovery, schedules and retention, notification delivery, firewall rollback, WAF policies and incidents, domain suspension, joined subdomain creation, independent database grants, DNS modes and setup wizard, WordPress inventory caching, direct-session SSH/2FA handling, customer terminal welcome, and compressed 90-day web-log history.

Validation is intentionally split into focused implementation tests and one combined release gate. Focused backend and browser checks cover ownership boundaries, recovery selection, encryption/tamper failure, retention, mail/DNS/PHP/domain recovery, firewall rollback, WAF policy, database grants, setup state, direct versus impersonated sessions, terminal startup, log rotation, Evo/Paper themes, keyboard/mobile layouts, and the custom-logo first paint. The signed release pipeline runs the complete backend suite and a fresh production frontend build before publication.

The following acceptance items require infrastructure that is not present in this workspace and remain recorded rather than simulated as real-provider success:

- Live AWS S3, Backblaze B2, and Google Drive round trips with customer-owned test credentials.
- A second isolated host for fresh-connection firewall lockout, crash, reboot, IPv6, and custom-SSH-port trials.
- Destructive full-account restore or live source-account migration. Only disposable accounts are authorized for those checks.

These dependencies do not weaken the backend restrictions: Drive incremental mode is rejected, destination secrets remain write-only/encrypted, pending firewall changes have an out-of-process rollback path, and customer authorization is enforced on direct recovery URLs.

## 6. Requirement traceability

| User requirement | Work package |
| --- | --- |
| JetBackup-like backup interface, functionality, destinations and jobs | B1–B6 |
| All accounts first, overview of actual backups, latest/older backup view | B1 |
| Download and restore selected recovery points | B1–B2 |
| Incremental, compressed and archived modes, properly tested | B2, Q1 |
| SSH username/password or key authentication | B3 |
| Reindex, config export, speed test, browse, validate, disable/delete, visibility | B4 |
| Dedicated Backups category for admin/customer, Evo/Paper | B5, U0 |
| Amazon S3, Backblaze S3, Google Drive, no incremental Drive, SFTP | B2–B3 |
| Backup notification plugins: email, Telegram, webhook | B6 |
| Firewall/WAF next, simple configurable controls and protection from lockout | S1–S2 |
| Customer domain suspend/unsuspend | H1 |
| Subdomain first, parent directly after it, retain correct document-root behavior | H2 |
| Database users, user/database assignment and missing cPanel/DA operations | DB1 |
| Explain present DNS; Cloudflare/local/cluster choices at setup | Findings, DNS1 |
| Existing-server DNS section and mode changes | DNS1 |
| Setup wizard: hostname, webmail/phpMyAdmin records, SSL, DNS, MaxMind, CF token | SETUP1 |
| WordPress plugins reloading/no list and speed | WP1 |
| SSH Keys customer-session error | AUTH1 |
| Terminal ASCII art instead of plain bash startup | TERM1 |
| Webserver logs, per-domain level, 90 days, size rollover/compression | LOG1 |
| User 2FA session error | AUTH2 |
| Preserve previous UI fixes; wait for batch testing/major release | U0, Q1 |

Implementation was approved. External storage/OAuth credentials and an isolated firewall test target are still required to close the real-provider acceptance items above.
