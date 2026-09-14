# Active product expansion goal

The complete user objective remains active. A checked item requires implementation and verification, including live-server evidence where relevant. No item is complete solely because its code exists.

- [x] WordPress: explicit scan/import and refresh; soft deletion of records with suppression until manual rediscovery; hard deletion of selected installation files and owned database with confirmation and tenant isolation.
- [ ] Backups: usable admin and customer backup system, reusable jobs, incremental snapshots, inclusion/exclusion filters, notification channels, SSH destinations, retention and restores. Verify restore contents and unchanged-file deduplication, not merely successful commands.
- [x] Domains/subdomains: independent site roots/public_html, DNS records, consistent creation workflow and ownership.
- [x] Mail: fix missing mail-domain provisioning and verify mailbox creation.
- [x] WordPress URLs: functional http/https and www/non-www installation selection, login and clone compatibility.
- [x] Applications: separate Python App and Node.js App navigation and user workflows.
- [x] phpMyAdmin: provision and verify real database-scoped access.
- [x] Panel SSL: issue and serve a valid certificate for the requested panel hostname, with renewal.
- [x] Panel ports: default shared admin/customer port 2222; admin configuration supports changing both ports, preserving access and enforcing intended role behavior.
- [ ] Direct interactions: database and SSL names/actions first, then audit other comparable lists; accessible desktop/mobile management views.
- [x] Terminal: configurable BORON ASCII welcome from admin configuration, suppress default Ubuntu status/MOTD in admin terminal; preserve usable prompts and appropriate customer behavior.
- [x] PHP: account default version inherited by new sites; per-site override dropdown; Lite/Moderate/Max limit presets and editable Custom selected by default.
- [x] Typography: improve font and dashboard icon-label sizing with local assets and no performance regression.
- [x] Security/time: admin/customer 2FA setup, recovery, reliable clock synchronization, drift/unsynchronized state detection and actionable diagnostics. Do not claim absolute immunity to host/network failure.
- [ ] Final build, targeted and broad regression checks, real workflows, deployment, and requirement-by-requirement completion audit.

## Initial evidence

Database and SSL names were plain spans, with controls in overflow menus. First edits add clickable names, visible manage controls and detail dialogs. These are not yet considered verified or deployed.

`daemon/handlers_mail.py:create_mailbox` rejects absent MailDomain rows. `daemon/pma.py` returns `pma_url=None` when the phpMyAdmin hostname is unset. Existing backup infrastructure has local/rclone destinations, tar artifacts, scheduled jobs and customer restores; incremental repositories and job filters need implementation.

Subdomains will be ordinary owned sites with their own document root and DNS records, with a convenient subdomain creation flow.

Research starting points: [restic backup documentation](https://github.com/restic/restic/blob/master/doc/040_backup.rst), [Ubuntu time synchronization](https://ubuntu.com/server/docs/how-to/networking/chrony-client/). Ubuntu 24 package behavior must be verified on this server rather than inferred from newer Ubuntu defaults.

## Progress — first implementation batch

- Database and SSL names now open management dialogs, with visible Manage controls. Production build and four browser checks (both themes × light/dark) passed; deployment is pending.
- Mailbox creation now provisions a missing MailDomain for an existing active hosting domain, and can repair a missing cache row. Twenty-six mail tests passed. Live mailbox verification is pending.
- Public DNS queried via 1.1.1.1 returns `boron.sitecountry.com A 104.234.179.66`; the local resolver initially returned a stale negative answer.
- Live clock inspection: `NTP=yes`, `NTPSynchronized=no`; timesyncd logs show repeated UDP/123 timeouts. Outgoing traffic is allowed by UFW. Chrony 4.5 was installed, replacing timesyncd. `scripts/install_time_sync.sh` adds authenticated Cloudflare NTS and enables chrony; synchronization still needs verification and health-monitoring integration.
- Current logs: `/root/boron-setup/expansion-build.log`, `expansion-interior-tests.log`, `expansion-mail-tests.log`, `expansion-chrony-install.log`, `expansion-time-config.log`.

The full original checklist remains open; the first batch does not substitute for completing the remaining requirements.

Clock follow-up: chrony selected `time.cloudflare.com`; live `chronyc tracking` reports `Leap status: Normal`, and `timedatectl` reports `NTPSynchronized=yes`. `chronyc authdata` confirms negotiated NTS keys/cookies. The fresh installer now calls the time setup script. Time-health UI/watchdog and 2FA workflow verification remain unfinished.

Next steps: implement persistent WordPress discovery/removal semantics and URL options; build incremental job-based backup storage/SSH workflows; resolve phpMyAdmin hostname/bootstrap and panel certificate/port configuration. Continue the complete checklist, including deployment of the verified first-batch UI and mailbox changes.

## Progress — address selection and application navigation

- WordPress address options are deployed: HTTP/HTTPS and www/non-www, with actual saved WordPress home/siteurl values and clone inventory persistence. OpenLiteSpeed maps unclaimed www names to their parent site while preserving explicitly configured hosts. HTTPS www installations cause subsequent certificate issuance to include the www name.
- Live testing found and fixed an older PHP-helper bug that dropped subdirectory paths. A fresh installation now persists the exact HTTP www folder in both WordPress options. Only this batch’s QA site was repaired; unrelated sites were not rewritten.
- Mailbox creation on an unprovisioned QA mail domain and real IMAP authentication passed (`mail-provision-proof.log`). The earlier database and SSL detail dialogs are deployed, with browser verification recorded in WORDPRESS-VERIFICATION.md.
- Separate Python App and Node.js App customer routes, dashboard entries, search aliases and creation flows passed in both themes.
- Validation: 167 affected backend tests, four direct PHP-helper tests, 24 browser tests, and two final wizard checks passed. Live address tests cover a fresh installation, HTTP and HTTPS www clones, served pages and login destinations. Development hostname routing uses local test resolution and the development certificate; it is not evidence of public DNS or trusted certificate issuance for QA names.
- Live removal verification is complete: the user explicitly approved deletion of the new `plainhttp` QA clone after automatic review requested specific authorization. Job 30 removed only that clone’s files, database and database user. All other installations remained registered; retained QA sites returned HTTP 200 on a follow-up check with a 45-second timeout. No hosting account was deleted. Soft removal kept the site online and a manual scan restored its panel record before the approved hard-removal test.

Routing and certificate references: [OpenLiteSpeed configuration](https://docs.openlitespeed.org/config/), [Certbot certificate expansion](https://eff-certbot.readthedocs.io/en/stable/man/certbot.html).

- Live HTTP www and HTTPS www one-click login reached authenticated WordPress dashboards; token reuse was rejected. A fresh corrected-helper installation preserved the full selected subdirectory in actual WordPress home/siteurl options.

## Progress — incremental storage foundation

Encrypted local and SSH/SFTP snapshot storage now backs up raw files, reuses unchanged content, verifies staged restores, supports single-file/directory selection and filters, and enforces account ownership during browsing/restoration/retention. SSH requires pinned host keys and dedicated private credentials. CPU/IO priority and worker-thread limits reduce interference with hosted sites. The installer includes restic.

This is a storage foundation, not a completed backup product. Persistent reusable jobs, scheduling, notifications, admin/customer UI and applying verified restores to accounts remain required. See INCREMENTAL-BACKUPS.md for the integration contract and test coverage. The overall backup checklist stays unchecked.

## Progress — saved incremental backup jobs

The development backend now includes persistent destinations, reusable policies, frozen per-account run settings, account/component/path filters, local/SSH initialization, administrator recovery-key export, full/incremental modes, retention, selected email/webhook channels and customer-scoped history/browsing. The existing scheduler invokes these jobs. Account and repository file locks coordinate workers, and startup handles interrupted work. Tests restore actual job output and check retention and cross-account authorization; 69 job/archive/RPC regression checks passed before the final duplicate-notification check.

This batch is not deployed yet. The admin/customer backup screens, applying staged restores, broader configuration recovery, coordination with other account operations and live workflow verification remain required. The complete product goal and backup checkbox remain open.

## Progress — backup management UI

The development UI now exposes destination and reusable-job management, SSH setup and recovery-key export, account/component/path filters, schedules, retention, selected notifications, manual execution and run history. Customers can inspect their own scheduled recovery points alongside existing archive backups. Both theme dashboards/search include the admin Backup Manager. Long-form action buttons remain visible on mobile, and recovery browsing starts with named account folders.

Production build and 12 backend checks passed. Browser verification covers both themes, light/dark modes, mobile dialog controls, job request payloads, explicit recovery-key download and customer history. Applying snapshot restores and live deployment remain required; the overall backup feature is still incomplete.

## Progress — snapshot file restoration

File restore jobs now verify staged contents, capture previous file versions, apply selected files/folders or all captured account files as the account UID, and preserve unrelated/new files. The UI exposes selection, typed confirmation, progress and previous-file recovery in both themes. Protected root-managed PHP runtime files remain under panel control. Snapshot and legacy archive jobs now coordinate their queues.

Database/mail/configuration restore, broader backup metadata, retention for pre-restore recovery points, final interaction audits and live deployment remain unfinished. The complete backup/product goal stays open; file-only restoration does not satisfy the full backup system requirement.

## Progress — database backup/restore transport

The snapshot exporter now streams raw SQL with the server's actual hosting privileges, fixing the previously unverified `--events` failure for ordinary WordPress databases. Real isolated MariaDB tests verify Unicode/binary contents through export, encrypted backup-job snapshots and import; temporary imports use exact database grants, reject system schemas and filesystem commands, and clean up their credentials. Unsupported SQL objects cause an explicit failure instead of an incomplete backup.

Database restore job/API/UI integration, previous-database recovery and deleted-database recreation remain required. No live database permissions were changed and no new backup code has been deployed yet. The full goal remains active.

## Progress — queued database restore and previous-version recovery

Database restores now run through the persistent account-scoped queue/API, verify
selected SQL snapshot files, save current databases to an encrypted recovery point,
and support recovering that previous version. Startup cleans abandoned temporary
import logins before queue recovery. This remains development code. Deleted-database
reconstruction, exact schema replacement, database UI, mail/config restore, safety
retention and deployment are still required; all other open product requirements
remain unchanged.

## Progress — database restore selection in both themes

Database restore selection and previous-version recovery are now connected to the
customer recovery-point dialog. The snapshot-backed catalog is account-scoped and
disables database entries whose ownership registration has been removed. Restore
history distinguishes files and databases. Remaining backup requirements include
deleted-database reconstruction, exact schema replacement, mail/config restoration,
safety-snapshot retention, interaction audits and live deployment. The full product
goal remains open.

## Progress — complete ordinary-table replacement

Database restore now removes tables created after a snapshot and imports all captured
ordinary tables. Previous-version recovery restores the later-created tables from
the saved recovery copy. Cross-database foreign keys and unsupported SQL object types
fail explicitly before replacement. This supersedes the earlier table-preservation
limitation, but does not complete deleted-database reconstruction, mail/config restore,
safety retention or deployment. The full product checklist remains active.

## Progress — PHP versions and resource templates

The PHP page now exposes account default and per-site version selectors together,
including returning a site to inheritance. Available versions come from the server.
Lite, Moderate and Max templates populate editable resource fields; Custom is the
default and resumes automatically on manual edits. Changes require Save and use the
existing validated account settings endpoint. Preset details are in PHP-CONTROLS.md.
Live deployment and served-PHP verification remain required, along with the other
open backup, server configuration and product requirements.

## Progress — configurable terminal welcome

Branding now provides a private admin terminal banner editor, preview, save and
reset to BORON. New admin web-terminal sessions use a quiet interactive SSH shell;
customer sessions retain their login behavior. The real QA SSH check verified no
Ubuntu MOTD, a working prompt and account UID isolation, with ephemeral key cleanup.
Build, branding/terminal regressions and four theme browser checks passed. See
TERMINAL-WELCOME.md. Deployment and final integrated verification remain pending;
the entire original product checklist stays active.

## Progress — independent subdomain sites

New addon/subdomain sites now have their own public_html under their full domain
name; existing recorded roots remain unchanged. The creation dialog provides an
owned-parent selector and full-name preview. Backend ownership checks prevent
cross-account DNS modifications, and zone lookup chooses the most specific match.
Real ACL tests prove web-server access without granting unrelated accounts access.
See DOMAIN-ROOTS.md for validation and remaining live deployment/DNS checks.

## Progress — clock health and restart protection

The installer now validates chrony configuration and applies bounded automatic
restart after service failure. This protection is live and chrony has re-synchronized.
Development health UI reports offset, uncertainty, stale measurements and sync failure;
the existing monitor now includes clock transitions and notification cooldown. The
probe is cached to keep dashboard polling inexpensive. CLOCK-HEALTH.md records live
evidence and boundaries. Deployment of UI/monitoring and the complete admin/customer
2FA workflow audit are still required; all other remaining requirements stay open.

## Progress — panel certificate automation foundation

Current live inspection confirmed a self-signed panel certificate and no HTTP route
for its hostname. Development now has a dedicated static HTTP-01 vhost, collision
checks, certificate issuance helper and stable renewal deploy hook with key/name/date
validation, served-certificate verification and rollback. The real certificate has
not been issued. Admin configuration, live deployment/issuance/trust/renewal checks
and separate configurable ports remain required; see PANEL-TLS.md.

## Progress — shared/separate panel listener foundation

A single API process now supports shared or separate admin/customer sockets, with
2222 as the fresh-install default. Role restrictions use the real local socket and
cover login, 2FA, session/token requests and terminal WebSockets. Firewall protection
and certificate verification cover both listeners. Real two-socket tests prove Host
header spoofing cannot cross roles. The live listener remains unchanged at 9443;
admin configuration/change rollback, TLS issuance and live migration remain required.
See PANEL-PORTS.md. The full goal is still active.

## Progress — administrator port-change workflow

The development admin Panel Settings page now previews and changes shared or
separate ports through persistent asynchronous jobs. Port transactions preserve
configuration and metadata, admit local firewall ports, verify both HTTPS listeners
against the installed certificate and roll back failures. Startup recovery recognizes
verified completed changes or restores the private recovery journal. API endpoints
are admin-only; dashboard search includes port/listener terms. Focused backend tests
passed 48 checks; theme/mobile browser verification and build evidence are recorded
in PANEL-PORTS.md and `/root/boron-setup/panel-settings-*` logs.

This feature is not deployed. The live panel still uses 9443 and its current
certificate. The next deployment work must integrate trusted panel TLS and verify
real shared/separate listeners, default 2222 and recovery on this server. The full
original checklist, including unfinished backup/restore and phpMyAdmin work, remains
active and must be audited before completion.

## User-added second phase — execute only after the original goal is finished

Added by the user on 2026-09-13 during live panel access verification. These are
part of the continuing goal, not replacements for any original requirement. The
user explicitly requires finishing the initial goal before starting this phase,
and authorizes routine implementation decisions while unavailable. Completion of
the overall goal must include both phases; do not mark it complete at the initial
phase boundary.

1. Reorder menus into this sequence, preserving role-appropriate access:
   - Domains, Subdomains, FTP accounts, SSL certificates, Databases, DNS.
   - Email Accounts, Email Settings, Email DNS Records.
   - WordPress section.
   - Backups section.
   - Node.js, Python, advanced options including Terminal and Redis.
   - Other features: logs, developer tools, change password, 2FA, security,
     processes and remaining tools.
2. Build a built-in filesystem malware scanner using suitable safe open-source
   tools plus Boron-specific checks. Detect and help block common WordPress
   compromises, malicious scripts and file exploits. This scanner is explicitly
   file/script based, not a network or port scanner; network controls belong to
   the firewall feature below.
3. Provide a server-level miniature CSF-style firewall utility: block/unblock
   ports and configure bypass IPs permitted to access all ports.
4. Provide editable OpenLiteSpeed administration, not a read-only surface; allow
   administrators to view available OLS credentials or reset its admin password
   from Boron. Do not claim that an existing one-way password hash is recoverable.
5. Provide administrator SSL certificate management and domain issuance directly
   from the admin interface without entering a customer session.
6. Add three or four prebuilt package templates to simplify package creation.
   Prefer dropdown choices to manual entry wherever sensible throughout the UI.
7. Manage multiple server IPs: assign dedicated IPs to users, mark one or multiple
   addresses as shared, support random shared-IP allocation and a chosen default
   address for new users.
8. Provide DirectAdmin-like administrator account backup/restore with a portable,
   universal account archive that can be restored on another Boron server.
9. Provide interfaces for importing both cPanel and DirectAdmin accounts.
10. Provide an administrator disk/resource usage utility showing disk, network,
    memory and related server usage.
11. Implement reseller users, reseller plans and a reseller panel with appropriate
    account ownership and permissions.
12. Provide prebuilt HTML account suspension templates.

Every item requires implementation and appropriate behavioral/UI/live validation,
not merely the existence of a menu or stub. Existing related features should be
inspected and extended. Original backup/restore, phpMyAdmin, access, PHP, clock/2FA
and usability requirements remain in the initial phase and keep their full scope.

DNS steering: the user reports phpmyadmin.boron.sitecountry.com is corrected and
asks to retry after propagation. Continue independent initial-phase work while
periodically verifying DNS; do not repeatedly ask the user while they are away.


## Live deployment — panel access verified 2026-09-13

The accumulated development build is deployed under `/opt/boron`, with the new
API service launcher. The initial deployment preserved 9443 and verified the API,
admin login and configuration RPC. Recovery backup:
`/root/boron-setup/expansion-before-20260913-202747` (private code/config/database).
The OLS root-ownership compatibility fix is also deployed.

The requested panel hostname now serves a trusted Let's Encrypt certificate.
Public-hostname HTTP challenge retrieval, normal TLS trust validation, a Certbot
renewal dry run and its real deploy hook all succeeded. The certificate expires
2026-12-12 and the existing twice-daily renewal cron invokes the stable hook.

Live configuration jobs 1–3 moved shared access to 2222, tested separate admin 2222
and customer 2223 with wrong-role rejection, and restored final shared 2222.
Admin/customer password login and retained WordPress inventory passed. The
customer QA account had no PanelUser identity; a customer-only identity was created
for that existing isolated account using its privately saved test password. The
temporary 2223 firewall admission was removed. Both real themes passed browser
checks on `https://boron.sitecountry.com:2222/app`, with trusted TLS enabled.

Regression evidence: 2031 full-suite checks passed and two optional tests skipped;
one fixture failed due to leaked daemon umask. The logging fixture now restores
umask and the ACL fixture matches the actual explicitly chmodded account home.
Thirty ordered logging/domain tests and the real ACL check under umask 027 passed.
Eighty-four OLS/TLS checks passed after the live ownership fix. The initial live
browser assertion used incorrect text casing; the corrected checks passed both
themes. See `/root/boron-setup/expansion-predeploy-tests.log`,
`domain-order-fix-tests.log`, `domain-daemon-umask-proof.log`,
`panel-tls-owner-tests.log`, `panel-tls-live.log`, `panel-ports-live-resume.log`, and
`panel-access-live-browser-final.log`. External crawler verification of the
nonstandard port was unsupported; DNS-based browser/API checks ran on this server.

phpMyAdmin packages are installed without replacing the web server. Public DNS
now resolves phpmyadmin.boron.sitecountry.com to 104.234.179.66 from 1.1.1.1 and
the authoritative Cloudflare nameserver. Vhost/SSL, one-click database access
and renewal integration remain unfinished.
The user reports the record corrected and requests retrying after propagation;
continue without repeated questions while they are away. Remaining original
requirements must finish before the recorded second phase begins.


phpMyAdmin follow-up findings for initial-phase implementation: `_challenge_plan`
in `daemon/ssl.py` currently special-cases webmail but not phpMyAdmin, and the
standard SSL deploy hook also lacks the phpMyAdmin refresh branch. The current
signon template reads then unlinks the token file, which does not prove atomic
single-use under concurrent requests; address this before exposing the service.
Verify secure session/caching/referrer behavior and real database-scoped access.
OLS may require the same non-root docroot-owner compatibility handling as the
panel challenge root; keep executable package files unwritable by the PHP worker.
The package is installed at `/usr/share/phpmyadmin`; token cleanup cron already
exists. Do not start second-phase additions until the original checklist is done.


### phpMyAdmin live completion — 2026-09-13

DNS propagation is complete. phpmyadmin.boron.sitecountry.com serves a trusted
Let’s Encrypt certificate; issuance and simulated renewal including the OLS
certificate deploy hook passed. Real customer browser launches in Evolution and
Paper Lantern rendered the QA WordPress database and its tables, kept the popup
opener isolated, and rejected replay of consumed signon tokens. No hosted tables
were modified. Evidence: `/root/boron-setup/pma-live-setup.log` and
`/root/boron-setup/pma-live-success.log`; screenshots `pma-live-evolution.png` and
`pma-live-paper-lantern.png` in the same directory.

Live validation exposed three package integration issues, now fixed: the PHP
namespace needed the token-directory mount; Ubuntu loads configuration from its
vendor-declared `/etc/phpmyadmin/config.inc.php`; and root-managed phpMyAdmin
assets link outside its docroot into `/usr/share/javascript`. Customer vhost
restrictions remain enabled. Token directory DAC remains root:www-data 0770.
Only the www-data service namespace/PHP workers were refreshed. The supported
`unmount_ns -u 33` utility was required because `lsnsctl --uid 33 unmount` rejects
UIDs below its customer minimum. No customer namespace minimum was changed.

Final OLS/phpMyAdmin regression run: 91 passed (`pma-assets-tests.log`), in addition
to the earlier real SQL isolation/concurrent redemption checks and frontend build.
The broader initial checklist and queued second phase remain active.

### Backup recovery-copy retention — 2026-09-13

Commit `a5f5454` implements separate retention of successful pre-restore copies
using each job’s configured recovery-point count. Failed/interrupted restores and
queued/running recovery sources remain protected; history clearly marks expired
copies. Actual encrypted-repository tests prove deletion, retained-copy recovery,
and retry after interruption between repository deletion and metadata update.
Twenty-one backend checks and eight theme/mode browser checks passed; production
build passed. See `docs/INCREMENTAL-BACKUPS.md` for the behavior and evidence paths.

Deployed after confirming all WordPress, snapshot and legacy backup/restore queues
idle. Recovery code archive: `/root/boron-setup/safety-retention-before/code.tar.gz`.
The immediate login probe raced API startup and received connection refused;
the subsequent probe succeeded: health 200, UI 200, login 303, administrator
identity 200. boron-api, boron-provisiond and lshttpd all reported active. This is
deployment/access evidence; it does not substitute for the unfinished full live
backup/restore workflow audit. Deleted database reconstruction, mail/configuration
restore and all other unchecked initial requirements remain active, ahead of the
queued second phase.

### Database reconstruction foundation — 2026-09-13

Added private, encrypted-snapshot database recovery metadata and a validated
primitive that recreates an entirely absent database/login pair while retaining
its original password and charset/collation. No existing database/login is adopted
or overwritten. Forty-six backend checks passed, followed by 12 final checks of
actual snapshot metadata and SQL reconstruction; read-only live capture validated
nine QA databases. See `docs/INCREMENTAL-BACKUPS.md` for evidence and limits.

This remains development work: customer selection/queue integration, partial
resource reconstruction, registration/conflict coordination, interruption recovery
and old-snapshot compatibility are unfinished. Live databases were not deleted or
recreated. Do not mark the backup checkbox complete based on this primitive; the
full original scope and queued second phase remain active.

### Queued database reconstruction — 2026-09-13

Connected private snapshot metadata to the catalog, queued restore worker and
both-theme selection UI for fully deleted database/login pairs. The real encrypted
backup/deletion/reconstruction test restores WordPress data, original credentials,
collation and account registration. A name reused after queueing is rejected and
its new contents remain intact. Added cross-process coordination for covered SQL
ownership mutations and validated metadata parsing with private temporary staging.

Evidence: 97 broader regression checks, three final reconstruction/coordination
checks, six metadata-reader checks and the final name-reuse integration test passed.
Four deleted-database selection browser cases and the production build passed.
Detailed logs/limits are in `docs/INCREMENTAL-BACKUPS.md`. This remains development
work: partial-resource repair, abrupt-interruption reconciliation, mixed recovery
and remaining mutation-entry-point audit precede deployment. Full backup completion,
the other unchecked initial requirements and the queued second phase remain open.

### Partial database repair and deployment — 2026-09-13

Added owned partial-resource repair without resetting surviving passwords, and
persisted reconstruction markers that allow a newly requested restore to reconcile
an interrupted create/user/grant sequence. Tests verify repaired access, original
WordPress data, protected surviving resources, changed-password rejection and
mixed existing/deleted restore undo. Extended SQL mutation coordination through
staging, existing cPanel import steps and legacy restore workers.

Validation passed: 24 partial/metadata/recovery checks, 39 mixed/SQL/CRUD/concurrency
checks, 65 staging/import checks and 54 legacy restore/concurrency checks. See
`docs/INCREMENTAL-BACKUPS.md` for logs. Deployed after idle-queue preflight with a
code recovery archive. Correct health/UI/login/identity checks passed and services
were active. The first readiness probe used the wrong `/health` route; `/healthz`
is correct. No additional restart was needed and no live SQL data was removed.

Remaining backup work includes background-worker behavior under SQL lock contention
(interactive busy errors are appropriate, scheduled jobs should wait/requeue), full
live disposable-data lifecycle proof, mail/config restore and the overall backup
requirements audit. Original unchecked tasks and the queued second phase remain
active. Do not mark backup completion based solely on these development tests and
panel deployment checks.

### Background waiting and live SQL backup/recovery proof — 2026-09-13

Background SQL backup/restore/import workers now wait under contention, while
interactive management retains prompt busy responses. Registration metadata is
refreshed after waiting, and credentials/SQL export are coordinated. The change
was tested, committed as `324bf20`, deployed after idle preflight and verified via
live health/UI/login/identity checks.

A new disposable QA database was created, backed up, deleted and reconstructed
through the actual administrator/customer APIs. Its original password and sentinel
contents were verified, and all nine neighboring registrations were retained.
No existing WordPress database was selected for deletion or restore. A second
manual backup reported only 6,819 new bytes versus 4,123,104 on the first. Live
browser checks passed both themes for the recovery point, database selector and
completed restore history. Destination/policy 1, backup runs 1/2 and restore 1 are
retained as QA evidence; the policy is disabled and manual. Credentials are private.

See `docs/INCREMENTAL-BACKUPS.md` for test counts, log paths, verifier corrections
and retained artifact details. The backup checkbox remains open: mail/configuration
restores and full SSH/filter/notification/live workflow coverage are still required.
Cold catalog latency and the label “Scheduled recovery points” for manual jobs
also belong in the final UI/performance audit. Other initial requirements and the
queued second phase remain active.

### Email recovery metadata groundwork — 2026-09-13

Added private encrypted-snapshot mail metadata for SQL mailbox credentials, quotas,
status and routing/autoresponder settings, plus an internal missing-mailbox SQL
primitive that preserves Dovecot password hashes and refuses existing mailboxes.
Twenty-two tests passed against the real installer schema in an isolated MariaDB
instance, including Dovecot verification of the original fixture password and an
actual encrypted message/metadata round trip. A read-only live source check covered
the QA account's one domain and one mailbox. No real mail was modified or sent.

See `docs/INCREMENTAL-BACKUPS.md` for evidence and limits. This remains development
work, not a deployed customer mail-restore feature: Maildir application, cache and
ownership coordination, UI, previous-message recovery, mail configuration resources
and live end-to-end verification are still required. All other unchecked initial
requirements and the queued second phase remain active.

## Live verification — terminal and clock, 2026-09-14

The authenticated admin WebSocket at the public HTTPS panel endpoint was tested
against the existing wpdevqa account. It emitted the configured BORON banner,
showed an interactive prompt without Ubuntu MOTD/status/last-login text, and ran
a command with the QA account UID. Closing the WebSocket removed its temporary
SSH authorized key. The separate quiet SSH helper check also passed. Verification
script: `/root/boron-setup/terminal-websocket-proof.py` on this development server;
credentials and SSH key material were not included in output.

Panel API/provisioning, OLS, Dovecot and chrony were active during the check.
`chronyc tracking` reported normal synchronization with Cloudflare, approximately
0.000276 seconds slow system offset and 0.00107 seconds root dispersion. This is
current health evidence, not proof against future host/network clock failures or
a substitute for the outstanding admin/customer 2FA workflow checks.
Read-only live deployment preflight also found zero active WordPress operations,
legacy BackupJob/RestoreJob records, or SnapshotRun/SnapshotRestore records at
this checkpoint. Recheck immediately before any later service deployment.

## Live mailbox recovery deployment — 2026-09-14

The accumulated backup changes are now deployed after 240 combined regression
checks. A fresh synthetic QA mailbox passed live incremental backup, selected
mail restore, previous-mail recovery, and post-restore IMAPS authentication/content
verification. Real Dovecot supervision resumed mail after each switch and no
restore guards remained. Deployment retained port 2222/configuration and a private
rollback copy. Details and job IDs are in INCREMENTAL-BACKUPS.md. Backup lifecycle
cleanup and the remaining unchecked initial requirements are still open.

## Live served-PHP verification — 2026-09-14

A separate QA hosting account was created for runtime checks, so existing
WordPress/customer accounts were not changed. A temporary PHP endpoint confirmed
that account default changes switch the served interpreter, an explicit site
override takes precedence, clearing it restores inheritance, and a newly created
independent site inherits the current account default. The complete advertised
PHP 8.1/8.2/8.3/8.4/8.5 set served successfully.

Every directive in the Lite, Moderate and Max presets matched its actual web
request `ini_get` value. A custom 384M memory limit applied, and resetting overrides
restored the previously observed baseline directives. Temporary PHP probes were
removed; the QA site again inherits its account default. Existing four-theme-mode
Playwright results (`/root/boron-setup/php-controls-browser.log`) cover dropdowns,
Custom selected initially, preset application and switching back to Custom when a
field is edited. Together these satisfy the PHP checklist item.

Live proof scripts and private QA inventory are in
`/root/boron-setup/live-php-controls.py`, `live-php-version-matrix.py` and
`live-php-controls.json`. The latter contains generated QA credentials and is
root-only; no credential values are included in this repository or test output.
The remaining unchecked product requirements and queued expansion are unchanged.

## Live subdomain verification — 2026-09-14

Using the isolated PHP QA account, a managed local DNS zone was created for its
parent domain and `blog` was added through the deployed domain API with kind
`subdomain`. The resulting root was exactly
`/home/<qa-user>/blog.<parent>/public_html`, owned by that account. Identically
named temporary files in the parent and subdomain roots served different expected
content through the actual OLS virtual hosts. The local authoritative DNS answer
contained the automatically created A record pointing to 104.234.179.66.
Temporary probes were removed and the QA site/zone retained. This verifies local
managed DNS; Cloudflare and public DNS delegation were not changed or claimed.
The creation form explicitly explains that automatic records require a managed
zone, so external-DNS users know they must configure their provider.

Four browser cases passed for the parent dropdown, subdomain name preview, request
payload and mobile overflow across both themes/color modes. The Evolution light
mobile screenshot was inspected and its fields/actions were readable. Live proof
script: `/root/boron-setup/live-subdomain-proof.py`.
Four targeted backend cases also passed: independent roots, rejecting an unowned
subdomain parent, refusing another account's DNS zone without record mutation,
and choosing the most specific managed zone. The domains/subdomains checklist
item is now verified; remaining initial-goal requirements stay open.

## Live 2FA and clock verification — 2026-09-14

Both a QA customer panel identity and temporary QA administrators exercised the
deployed HTTPS 2FA flow: pending enrollment with QR/manual seed, valid-code
activation, eight recovery codes, password login stopping at the second-factor
challenge without an authenticated session, invalid-code rejection, successful
TOTP login, recovery-code login, rejection of recovery-code reuse, rejection of
disabling with a wrong password, and successful password-confirmed disabling.
Password-only login worked again after disabling. The main administrator identity
was not modified. Temporary identities were disabled, their 2FA credentials
cleared and all their sessions revoked; cleanup was independently checked.

The combined live sequence hit the existing per-IP login limit (HTTP 429); the
admin-only test honored Retry-After and then passed without changing rate limits.
Live scripts: `/root/boron-setup/live-twofactor-proof.py` and
`live-admin-twofactor-proof.py`. No live seeds, passwords or recovery codes were
printed or committed.

Twenty-five TOTP/clock backend tests passed, covering encrypted seed storage,
single-use recovery and drift/stale/unsynchronized/invalid clock diagnostics. Four
clock-health browser cases passed across both themes/color modes. Eight new
2FA browser cases passed across both roles, themes and modes, covering enrollment,
mobile layout, one-time recovery display and password-confirmed disabling; the
Paper Lantern dark recovery screen was visually inspected with synthetic codes.
Chrony remained active/enabled with normal synchronization, Restart=on-failure and
a five-second restart delay. This satisfies the security/time checklist item
without claiming immunity to future host or network failure.

## Typography and asset performance — 2026-09-14

Dashboard icon labels now render at 14px on desktop and 13px on mobile in both
themes (previously Evolution was 12px/11px and Paper Lantern 13px/12px). Evolution
uses the existing locally hosted Inter variable font; Paper Lantern retains its
native Arial appearance and requires no webfont. No font payload was added by
this change. Four browser checks across roles/themes confirmed computed sizes,
no mobile horizontal overflow, no remote asset requests, one Evolution font
within a 50KB budget and zero Paper Lantern webfonts. The Evolution mobile
dashboard screenshot was visually inspected.

Hashed Vite assets now receive public one-year immutable caching, while stable
index/theme-init files revalidate. The HTTP integration test covers font/JS/CSS
200 and ETag 304 responses, mutable entrypoints, unhashed files and missing assets.
Production build passed. Revision 592ff0a was deployed with a private rollback
copy at `/root/boron-setup/mail-recovery-before-20260914-022737`; panel health,
admin login and configuration RPC passed on unchanged port 2222. Live HTTPS
verified the 48,256-byte local Inter file, immutable headers, ETag 304 and no-cache
entrypoints. This improves repeat-visit caching without adding font requests,
external services or font bytes, and completes the typography checklist item.

## Terminal customization and customer isolation — 2026-09-14

The deployed admin branding API temporarily saved a multiline QA banner containing
literal shell-like text. A new authenticated admin WebSocket emitted the exact
configured text, retained a quiet interactive prompt without Ubuntu status/MOTD,
and executed a command as the selected QA hosting UID. The original banner was
restored and checked through the API in the test's cleanup path. Four theme/mode
browser cases passed for the editor, preview, save, reset-to-BORON and mobile
layout. Live script: `/root/boron-setup/live-terminal-branding-proof.py`.

The existing wpdevqa customer then authenticated with its own panel credentials.
Its own terminal accepted commands as its hosting UID, did not receive the
admin-only banner, and a WebSocket request for the separate PHP QA account was
rejected with HTTP 403 before terminal access. Closing each successful connection
removed its temporary SSH key. Live script:
`/root/boron-setup/customer-terminal-websocket-proof.py`. No existing passwords
were changed, credential/key values were not printed, and no customer account
configuration was altered. The terminal checklist item is now verified.

## Direct resource management follow-up — 2026-09-14

The comparable-list audit found remaining overflow-only actions in Node.js/Python
applications, FTP, Git and cron. Application, FTP and repository names now open
management dialogs, with a visible Manage button as an additional entry point.
Application controls use refreshed query data so Start/Stop and status remain
consistent after an operation; pending operations disable duplicate service
actions. The dialogs expose existing actions and retain destructive confirmation.
Cron labels and visible Edit buttons open the existing editor; DNS record names
open their corresponding record editor.

Domains already link to management pages, database and SSL names already open
management views, and DNS has visible edit/delete controls. Mailboxes and SSH
keys have visible removal controls, but mailbox management remains a candidate
for further usability work. The direct-interactions checklist remains open until
this final audit is complete.

Production frontend build passed. Four resource-management browser scenarios
passed (Evolution/Paper Lantern × light/dark), including keyboard activation,
service status refresh, restart failure without losing controls, logs, deletion
confirmation without DELETE requests, FTP password/path editors, Git deploy
editor, cron/DNS name activation, and 390px management dialogs without horizontal
overflow. These tests mock resource APIs; they do not claim live service mutation.
Evolution light's mobile application dialog was also visually inspected.
Existing database/SSL/DNS interior and Python/Node navigation regression coverage
also passed: six scenarios on the final build, ten browser scenarios total.

Revision `eb581cd` deployed successfully to the live development panel. Preflight
found no active WordPress or backup operations and verified the mail guard.
Private rollback copy: `/root/boron-setup/mail-recovery-before-20260914-024812`.
Post-deployment HTTPS health, admin login and configuration RPC on port 2222
passed; configuration remained unchanged. All five changed page bundles fetched
through public HTTPS matched the tested build byte-for-byte with immutable cache
headers. Deployment log: `/root/boron-setup/direct-management-deploy.log`.

## Mail preparation lifecycle — 2026-09-14

Completed mailbox restores now remove redundant decrypted extraction data and
offline rebuilt preparation trees after durable completion. Cleanup requires a
completed mail job and matching completed checkpoint, switch journal, safety
receipt and release intent. It removes only fixed data/ready-N directories using
descriptor-based symlink-resistant deletion inside root-private storage.
Interrupted/failed jobs remain untouched. Partial cleanup can resume, and startup
queues completed jobs whose preparation cleanup has not finished. Cleanup errors
retain a completed restore's successful state and its recovery evidence.

Live Maildirs, displaced sibling copies, encrypted recovery points and private
journals are deliberately outside this cleanup phase. Displaced-copy lifecycle
and the wider backup completion audit remain outstanding.

Ten dedicated cleanup checks cover completion gating, partial deletion/retry,
symlink escape prevention, receipt binding, preserved journals, failure state,
and startup retry. Combined cleanup/dispatch/metadata/staging regression:
30 passed in 154.42 seconds. A real encrypted-mail workflow assertion was then
added to require cleaned preparation after restore and undo.
The strengthened real SQL/restic/offline-Dovecot workflow passed separately
(88.72 seconds): preparation was removed after restore and undo; journal-based
finalization recovery and undo-of-undo still produced the expected messages.

Revision `8e21875` deployed successfully. Rollback copy:
`/root/boron-setup/mail-recovery-before-20260914-025851`. HTTPS health, admin login
and configuration checks passed with unchanged listener configuration. Startup
cleaned the redundant staging areas for completed QA mail restores 2 and 3.
The private before/after hash verifier confirmed unchanged live messages,
displaced messages and recovery journals for both jobs. Proof script and private
manifest: `/root/boron-setup/mail-preparation-cleanup-proof.py` and its JSON sibling.
No live mailbox or displaced sibling was removed. Backup completion remains open.
