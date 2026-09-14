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

## Displaced mailbox lifecycle — 2026-09-14

Completed mailbox restores now verify their encrypted safety inventory, restore
the displaced mailbox paths with restic verification, and compare directory/file
content digests before local disposal. The recorded former-Maildir inode is moved
with RENAME_NOREPLACE into a root-private quarantine beside the mailbox, so this
also works when mail and backup staging are on separate filesystems. Identity and
content are checked again after the move; active Maildir is never selected.

Private receipts distinguish ready/deleting/deleted phases. Retries inspect the
existing quarantine rather than replaying moves, and resume partial deletion.
Changed contents, changed directory identities, replaced quarantine roots and
links are retained for inspection. Empty quarantine removal is also resumable.
Startup retries unfinished cleanup. Recovery-point retention protects completed
mail restores until displaced cleanup succeeds, then applies normal policy.

Regression evidence: 37 cleanup/dispatch/metadata scenarios passed, including the
real encrypted restore, interrupted-finalization recovery, undo and undo-of-undo
with successful displaced cleanup. Nine dedicated filesystem scenarios passed
after adding the last empty-container crash case. A separate SQL retention test
proved that pending cleanup protects its snapshot and successful cleanup restores
normal eligibility. Sandbox-only test cleanup warnings concern old root-owned
pytest temporary directories; no assertions failed.

Revision `e0ff402` deployed successfully; rollback copy:
`/root/boron-setup/mail-recovery-before-20260914-030928`. Panel HTTPS, admin login
and configuration checks passed. Startup completed displaced cleanup for QA
restores 2 and 3. The live proof confirmed both displaced directories and empty
quarantine containers were gone, active message hashes and original journal
hashes were unchanged, and both retained encrypted recovery points restored the
original displaced-message hashes. Proof script:
`/root/boron-setup/mail-displaced-cleanup-proof.py`.
The successful-mail staging/displaced lifecycle is now verified in production;
failed/interrupted recovery evidence remains protected. The broader backup
requirements and final initial-goal audit remain open.

## SSH jobs and notification audit — 2026-09-14

The new end-to-end SSH job test creates a destination through normal key generation,
installs its returned public key in an isolated loopback SSH server, initializes
with a pinned host key, executes a reusable job with account/path/exclusion
filters, verifies unchanged-file deduplication, restores selected content through
the account restore worker and verifies its safety snapshot. Actual loopback SMTP
and signed HTTP receivers verify completion and failure notifications, subscription
validation, account recipient preferences and duplicate-worker suppression. The
webhook public-IP gate is replaced only for the exact test receiver; production
SSRF protection is unchanged. No external notifications were sent.

The failure test found that backup.failed was emitted but absent from the allowed
webhook catalog. Added it to backend validation/dispatch and frontend selection.
The frontend event choices now also include the already-supported DNS activation
event. Corrected the misleading empty-selection hint and disabled submission until
an event is selected, matching backend validation.

Final evidence: 30 SSH-job/webhook backend checks passed; two shared SSH storage
regressions (real restoration and unknown host-key rejection) passed. Four browser
checks passed across both themes and modes for failure-event selection, request
payload and empty-selection validation; production frontend build passed.

Current backup capability/gap audit: BACKUP-COMPLETION-AUDIT.md. Account
configuration and captured mail routing restore actions remain unfinished.

Revision `700be88` deployed successfully. Private rollback copy:
`/root/boron-setup/mail-recovery-before-20260914-032403`. HTTPS health, admin login
and configuration checks passed. The live backend event catalog and both public
HTTPS bundles (shared event list and webhook form) matched the tested source/build.
Existing webhook subscriptions were retained; no live webhook was created or sent.

## Complete cron configuration capture — 2026-09-14

Configuration backups previously stored only parsed panel cron jobs, losing
manual entries, environment lines and MAILTO semantics. New snapshots include an
account-bound, versioned raw cron_configuration payload from a single crontab
read; the displayed managed-job metadata is derived from those same lines.
Managed @hourly-style schedules now parse correctly instead of disappearing.

Added bounded configuration validation and a single-write restore primitive using
crontab -u for the bound account. It preserves complete tables, rejects foreign
account payloads and line-injection/oversized data before mutation, and lets the
crontab utility validate syntax before installing. The future configuration
restore coordinator must authorize the account and encrypt current settings
before calling it; no public restore endpoint invokes this primitive yet.

66 cron/backup-job checks passed, including an actual encrypted config snapshot
with manual entries, empty MAILTO and an @hourly managed job. On disposable QA
account pq0914020151, a real crontab round trip preserved the complete configuration,
invalid cron syntax left the current schedule intact, and the original table was
restored in cleanup. Private evidence: /root/boron-setup/cron-configuration-proof.py
and cron-configuration-before.json. No customer crontab was changed.
Configuration/DNS/PHP and mail-routing restore integration remain open.

Revision cea90ce deployed successfully; rollback copy:
/root/boron-setup/mail-recovery-before-20260914-033351. HTTPS, admin login and
configuration checks passed. Live cron/snapshot source files matched the tested
revision, and a post-deployment read confirmed the original QA crontab remained
intact. Only the disposable QA account was exercised; other accounts were untouched.

## Scheduled-task restore integration — 2026-09-14

Configuration recovery now exposes scheduled-task restore in snapshot details for
both themes. Customers explicitly confirm their account username before replacing
the complete crontab. The worker loads account-bound encrypted metadata, saves the
current table in a new encrypted safety snapshot, persists that recovery point,
then performs one crontab installation. Restore history exposes previous-schedule
recovery; undo also saves a recovery copy and can itself be undone.

The configuration catalog returns availability/counts without exposing raw cron
environment values. Older backups lacking complete crontab data are rejected with
an explanation. Account and repository ownership checks apply before decryption;
API scope checks prevent foreign-account access. Cron mutation handlers now share
the account backup/restore lock, preventing panel edits during recovery.

Validation: 57 cron/configuration tests passed, including real encrypted restore,
undo and undo-of-undo with isolated crontab transport; 13 cron handler/API checks
passed; eight browser scenarios passed across both themes/modes for scheduled-task
and database recovery. Frontend build passed. DNS/PHP configuration and mail-routing
recovery are still outstanding; this does not mark the broader backup goal complete.

Revision 58efeee deployed successfully; rollback copy:
/root/boron-setup/mail-recovery-before-20260914-034413. HTTPS, admin login and
configuration checks passed. Live HTTPS workflow on disposable pq0914020151:
destination 3, manual policy 3, configuration backup run 4, scheduled-task restore
4 and undo 5 all completed. Actual crontab contents matched the selected backup
after restore and the newer pre-restore configuration after undo; both restores
recorded encrypted safety copies. The original QA crontab was restored in cleanup.
Private script/state: /root/boron-setup/cron-snapshot-workflow-proof.py and
cron-snapshot-workflow.json. Configuration backups/recovery copies remain retained.
DNS/PHP configuration recovery and mail routing recovery are the next open backup
work; the full goal and queued expansion remain active.

## Complete PHP configuration capture — 2026-09-14

Configuration snapshots now include a versioned, account-bound PHP payload with
account default version, per-site version overrides, all six legacy ini fields,
extra directives and extension selections. Null values preserve inherited defaults;
an explicit empty extension list remains distinct from the server default set.
Administrator-controlled disabled-function rules are recorded separately as private
metadata and must not be applied by customer self-service restoration.

Capture reads all PHP tables in one SQLite read transaction, verifies current
account identity and domain ownership, and excludes neighboring accounts. A test
commits another settings change between reads and confirms the snapshot retains
one consistent view rather than mixing old/new values. Real encrypted job output
was restored and checked for versions, limits, extra directives and an empty
extension selection. Existing scheduled-task restore tests remained green.
22 PHP/configuration/job regressions passed (112.31 seconds). PHP restoration,
DNS recovery and mail-routing recovery remain open.

Revision 3899066 deployed successfully; rollback copy:
/root/boron-setup/mail-recovery-before-20260914-035412. HTTPS, admin login and
configuration checks passed. Existing manual QA policy 3 produced configuration
backup run 5. Its encrypted manifest was restored privately and the complete PHP
payload matched the live QA account's current settings across all three sites.
No PHP runtime settings were changed. Private proof script/state:
/root/boron-setup/php-configuration-backup-proof.py and its JSON sibling.

### PHP recovery metadata validation — 2026-09-14

Added account-bound normalization for saved PHP defaults, site overrides, limits,
extra directives and extension dependencies. Administrator function restrictions
are excluded from the customer recovery result. Missing/transferred saved sites
and unavailable PHP versions/extensions are rejected before any mutation.
The configuration metadata reader now shares its encrypted snapshot ownership,
private-path and recovery-job checks between scheduled tasks and PHP settings.
This is preparation for PHP recovery, not a shipped PHP restore action: worker
application, runtime reconciliation, undo and both-theme UI remain outstanding.

Validation: 22 PHP capture/validation tests and four existing encrypted scheduled
configuration tests passed. The new encrypted PHP metadata test initially failed
because its fixture attempted to overwrite create-once metadata; after fixing
only that fixture, its targeted rerun passed (24.78s). It verifies normal backup
loading, foreign-account rejection, recovery-copy job/path binding and the older
metadata error. No production PHP setting or deployment was changed in this step.

### PHP restore application and safety worker — 2026-09-14

Added transactional replacement of customer PHP defaults, owned-site overrides,
legacy limits, extra directives and extension selections. Administrator function
restrictions are preserved. The new PHP configuration worker encrypts the prior
settings and persists its safety snapshot ID before applying changes. Extension
scan directories are prepared before vhost refresh and PHP worker recycling.
Runtime errors trigger reapplication of the previous database settings and
runtime; a separate error reports when runtime rollback cannot be confirmed.
Unused extension scan directories are retained when switching to defaults rather
than deleting runtime files during recovery.

Evidence: all 26 PHP capture/validation/application tests passed (46.08s), including
safety-capture failure with no mutation, restore/reapply round trip, account and
administrator-policy isolation, and runtime failure/rollback failure. One actual
restic-backed PHP worker test passed (15.99s), verifying the encrypted previous
settings can be loaded and their snapshot ID is saved before runtime application.
Runtime calls in these tests are mocked; live OLS application is not yet verified.

This worker is not yet exposed through restore dispatch or the interface and has
not been deployed. Account mutation coordination, dispatch/API/UI integration,
restart/failure recovery audit and live restore/undo verification remain required.

### PHP recovery queue and edit coordination — 2026-09-14

The restore queue now accepts PHP settings as a separate configuration section,
validates availability, dispatches the PHP worker, and preserves section selection
through undo/redo. Configuration previews report PHP and scheduled-task
availability independently so an unsupported PHP setting does not hide a usable
crontab recovery point. The default PHP version, per-site PHP version, limits and
extension edit handlers now acquire the same nonblocking account lock used by
backup/restore execution, with identity revalidation after acquisition.

The interface still needs PHP restore controls and PHP-specific history/undo text.
This code is not deployed. Domain/lifecycle mutation interactions and daemon
restart behavior still require audit before live PHP recovery verification.

Validation: 32 PHP capture/application/coordination tests passed (58.25s); seven
real encrypted configuration tests passed (144.18s), including queue-level PHP
restore/undo/redo with mocked OLS calls. The existing PHP/domain handler regression
run passed 57 tests; one ACL fixture failed because the sandbox rejected chown to
its test UID. Its authorized unsandboxed targeted rerun passed (2.27s). No live
account settings were changed or code deployed.

### PHP recovery interface — 2026-09-14

Both themes now offer separate scheduled-task and PHP settings restore actions.
PHP preview shows the saved default version and site count, explains website
impact and administrator-policy preservation, and requires the account username.
Switching sections clears confirmation; pending restore blocks section changes.
Unavailable sections show their own reason. Restore history and undo confirmation
now identify PHP settings rather than incorrectly labeling all config recovery as
scheduled tasks.

Production build passed. Eight mocked-API browser tests passed (39.3s) across
Evolution/Paper Lantern and light/dark, covering PHP and scheduled-task restore,
section switching, exact payloads, typed undo confirmation and mobile overflow.
Deployment and live PHP runtime restore/undo remain pending; this UI does not
establish live recovery completion.
Two additional browser tests passed (15.3s), one per theme, verifying an unavailable
PHP recovery point displays its account/site reason, exposes no PHP submit action,
leaves cron recovery available, and sends no mutation requests while inspecting.

### Interrupted PHP runtime reconciliation — 2026-09-14

Startup now handles interrupted PHP restores separately: after acquiring the
account lock, it rebuilds runtime from the committed (transactional) database
settings and marks the interrupted operation failed with an explicit recovery
message. It never replays the restore or discards the encrypted previous copy.
If runtime reconciliation fails, history says it could not be confirmed and
retains the same undo snapshot. An interruption before the safety snapshot was
persisted is identified as occurring before PHP settings changed.

Domain add/remove and account suspend/unsuspend/reactivate handlers now use the
same account mutation lock, supplementing the PHP edit and termination guards.

Affected lifecycle/domain/staging/legacy full-restore regression: 71 selected tests
passed (150.34s; 88 unrelated tests deselected), using authorized disposable
filesystem ownership fixtures. This includes existing domain creation/removal,
account suspension/reactivation, staging compensation, and full-restore paths.
PHP/configuration regression: all 46 tests passed (289.18s), including simulated
process loss after database commit, startup reconciliation success/failure, and
successful encrypted undo after either outcome. Tests use real restic repositories
and mocked runtime service calls. Live deployment and real OLS verification are
still required; no customer PHP configuration was changed in this step.

### PHP recovery deployment — 2026-09-14

Deployed through commit 6295ae4 using the existing guarded deployment script.
Preflight found no active WordPress or backup/restore jobs and verified the mail
guard. Private code/config/database rollback copy:
`/root/boron-setup/mail-recovery-before-20260914-042747`.
Post-deployment HTTPS health, admin login, configuration RPC and backups page
passed on port 2222. Listener/configuration bytes matched the pre-deploy copy.
Deployment log: `/root/boron-setup/php-recovery-deploy.log`.

Live QA evidence: `/root/boron-setup/php-recovery-live-proof.py`, private state
`php-recovery-live-proof.json`, and log `php-recovery-live-proof.log` in the same
directory. Isolated account pq0914020151 only. Manual configuration backup run 6,
PHP restore 6, encrypted undo 7 and cleanup restore 8 all completed with safety
snapshots. Changed the QA default PHP version and memory limit through HTTPS API;
actual OLS responses confirmed the changed values, original values after restore,
changed values after undo, and original values after cleanup. Complete captured
PHP settings matched the expected state after each operation, preserving existing
site overrides and administrator restrictions.

Independent post-test verification confirmed original QA settings, removal of the
random temporary PHP probe, all four completed jobs, active boron-api,
boron-provisiond and lshttpd, deployed Python files matching the tested worktree,
and the served Backups frontend bundle matching the production build byte-for-byte.
No credentials were printed. Live process-crash injection was not performed;
interrupted recovery remains covered by the real encrypted repository tests with
mocked runtime calls. DNS and mail-routing restoration remain outstanding.

### Native DNS recovery metadata — 2026-09-14

Audit found the existing config backup used the DNS editor's simplified records:
PowerDNS disabled flags/comments were discarded and Cloudflare per-record proxy,
automatic TTL and settings were collapsed. Added native DNS capture bound to
account/zone/provider registration, with ownership/provider rechecks after reads.
PowerDNS rrsets retain SOA, disabled flags and comments. Cloudflare capture retains
individual record documents and uses the zone's registered token context without
including credentials in backup metadata. The legacy manifest DNS view is derived
from the same native capture, avoiding duplicate provider reads and inconsistent
snapshots between the two representations.

Provider references: https://doc.powerdns.com/authoritative/http-api/zone.html and
https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/list/.
This adds capture only. Native payload validation, record application, encrypted
undo, provider/record mutation coordination, UI and live DNS recovery remain to be
implemented and verified. Provider/delegation changes are not performed by capture.
Cloudflare pagination is not a globally atomic external snapshot; ownership/provider
rechecks detect control-plane changes, not arbitrary edits outside Boron.

Validation: seven final DNS tests passed (18.30s), including native disabled-record
preservation through a real encrypted restic backup, Cloudflare per-record fields
and transport-token isolation, foreign registration rejection, ownership/provider
change rejection, and legacy-view derivation. Two final API authorization/full
configuration backup tests passed outside the sandbox (12.04s; existing Starlette
warning). The earlier combined run was interrupted after 17 completed checks when
its sandboxed TestClient stalled; the remaining two checks were the targeted
unsandboxed run. No DNS records changed and this capture change is not deployed.

### DNS recovery validation and encrypted loading — 2026-09-14

Added local-provider recovery validation bound to the current account, zone ID,
and provider registration, including strict binding value types. Customers can
select an owned subset without being blocked by a different deleted saved zone.
Record owners must remain inside the selected zone; dnspython validates native
RDATA against its type. TTLs, disabled flags, comments, duplicate rrsets, DNS
meta-types and CNAME conflicts are checked. SOA, apex NS and generated DNSSEC
records are excluded from customer recovery; an apex CNAME is rejected because
it would conflict with the retained zone authority records.

The encrypted configuration loader now reads DNS through the existing snapshot
ownership/private-path/source-job checks. Cloudflare restore currently reports
unavailable until its separate native record validator/application is implemented.
This is not the final DNS recovery scope: both configured providers still need
supported restore/undo paths. No DNS mutation worker or UI is wired yet, and no
live DNS records changed.

Validation: all 17 final DNS tests passed (44.34s). The real encrypted backup test
now loads its DNS payload through the shared configuration loader and validates
that disabled records survive. Additional cases cover out-of-zone names, invalid
addresses/meta-types/TTLs/disabled flags, duplicate selection, binding type changes,
owned subset selection and apex CNAME rejection. This step is not deployed.

### Local DNS recovery application worker — 2026-09-14

Added local DNS application after an encrypted, persisted previous-state callback.
The worker captures only selected zones, revalidates account/provider bindings
before each write, and refuses to overwrite records changed during safety-copy
preparation. It sends one PowerDNS PATCH per zone containing deleted and replaced
customer rrsets while leaving SOA/apex NS/generated DNSSEC untouched. Readback
compares canonical DNS values, disabled flags, TTLs and comments. Failures retain
the previous copy and report that some records may have changed; completed-zone
progress is recorded for multi-zone work.

The configuration worker writes DNS safety metadata to the established private
recovery path, encrypts it, persists its snapshot ID before application, and can
load that copy for undo. This internal worker is not yet dispatched or exposed in
the UI. DNS mutation/provider-transition coordination, Cloudflare restore,
queue/API/UI integration, interruption audit and live verification remain.

Validation: 22 DNS tests passed (42.71s); the additional real-restic worker
restore/undo test passed (18.07s), verifying safety snapshot persistence before
provider writes and encrypted previous-record loading. A separate readback-mismatch
test passed (4.98s): provider success without matching records is not treated as
completed recovery. Provider writes use an in-memory test backend or mocked HTTP
transport; no live DNS record was changed and this step is not deployed.

### DNS mutation and provider transition coordination — 2026-09-14

Added a dedicated reentrant DNS mutation lock shared across threads and processes.
Snapshot DNS capture/application waits for it as background work; competing direct
edits fail promptly with a DNS-specific busy message. Protected paths include both
providers' native record/zone writes, dnsprovider routing, managed-zone creation and
removal, Cloudflare activation/status transitions/revert/proxy enable/termination,
and forced pool-account removal or legacy-token migration. Outer operations retain
the lock across provider calls and control-plane registration updates; nested calls
reuse the same lock rather than deadlocking.

This does not coordinate changes made outside Boron directly against a provider.
DNS recovery dispatch/UI and Cloudflare-native restore remain outstanding. No live
DNS record or provider registration was changed in this development step.

Validation: 37 recovery/coordination tests passed in the initial run; one low-level
PowerDNS test lacked a temporary lock-directory fixture and hit the read-only
sandbox path. After adding its isolated fixture, the targeted rerun passed (5.27s).
The 81 existing DNS handler/Cloudflare client/zone lifecycle tests passed (82.40s),
including activation/resync/revert and nested provider operations. Lock tests cover
cross-process exclusion, thread waiting, release after exceptions and reentrancy.
Three selected Cloudflare pool deletion/migration tests also passed (8.38s;
14 unrelated tests deselected). Changes are committed for further integration,
not deployed; current live DNS and provider state remain unchanged.

### DNS recovery queue and combined configuration preview — 2026-09-14

The configuration preview now decrypts the private manifest once for cron, PHP
and DNS, rather than repeating the archive read per section. DNS preview lists
available/unavailable saved zones with reasons and record counts without exposing
record contents. Restore requests require a nonempty, distinct selection of
available zones; the saved selection is retained through undo/redo. Added DNS zone
selection to the API body and connected the DNS worker to configuration dispatch.

This remains development-only: the interface/history/undo labels still need DNS
support, and Cloudflare-native restore remains outstanding. No deployment or live
DNS mutation was performed in this step.

Validation: three real encrypted DNS queue tests passed (67.03s), covering one-read
catalog summaries, restore/undo/redo, preserved zone selection and invalid/foreign
selection rejection. Nine existing encrypted PHP/cron configuration tests passed
(152.37s). The API account-authorization/forwarding test passed (10.07s, existing
Starlette warning) with authorized local TestClient transport. DNS backend writes
in tests are simulated; this does not establish live provider recovery completion.

### DNS restore controls in both themes — 2026-09-14

Added a DNS records section to the shared configuration restore form. It lists
saved zones with record counts or unavailability reasons, requires a nonempty zone
selection and typed account confirmation, and resets confirmation when selections
or sections change. It explains that records created after the backup will be
removed and that website/email behavior can change. Server-managed authority and
DNSSEC settings remain preserved by the worker.

Restore history identifies DNS records, and previous-version confirmation names
the affected zones with a DNS-specific action. PHP and scheduled-task flows remain
separate. Production build passed (28.27s). This is not deployed; live DNS recovery
and Cloudflare-native restoration remain outstanding.

All 16 browser tests passed (1.4m) against the production build with mocked APIs:
cron/PHP/DNS restore and undo across both themes and light/dark modes, exact DNS
selection payloads, unavailable-zone disabling, confirmation resets, mobile
horizontal overflow, and unavailable PHP/DNS sections retaining cron access.

### Interrupted multi-zone DNS recovery — 2026-09-14

Startup history now distinguishes DNS interruption before safety-copy persistence
(no records changed by that job) from interruption after writes may have begun
(the encrypted previous records remain available for undo). Startup does not replay
DNS writes; provider records already represent the applied state, unlike PHP's
separate database/runtime reconciliation requirement. Completed-zone summaries and
original zone selection remain attached to the interrupted operation.

Added two-zone process-loss tests before and after the second provider write,
including complete encrypted undo, plus pre-safety interruption and active-account
lock observation. These use actual restic snapshots with simulated provider writes;
live interruption injection and deployment were not performed.

All six queue/interruption tests passed (107.50s), including both multi-zone crash
positions and successful undo after startup classification. The additional active
account-lock test passed (8.11s), confirming startup leaves an ongoing worker's
status and DNS records untouched. This change is not deployed; Cloudflare-native
restore and live local DNS recovery verification remain pending.

### Local DNS recovery deployed and verified live — 2026-09-14

Deployed through f1db8d9 using idle-job/mail-guard checks and private rollback copy
`/root/boron-setup/mail-recovery-before-20260914-050206`. Deployment log:
`/root/boron-setup/dns-recovery-deploy.log`. Authenticated HTTPS health/configuration
checks passed on port 2222 and listener configuration bytes were preserved.

Live proof: `/root/boron-setup/dns-recovery-live-proof.py`, `.json` and `.log`.
Only isolated local zone pq0914020151.boron.sitecountry.com was exercised. Manual
backup run 7 captured its original native records; a uniquely named TXT record was
then created through the HTTPS API and confirmed in an authoritative DNS answer.
Restore 9 removed that post-backup record and matched the complete normalized
original zone. Undo 10 restored the TXT record and previous zone. Cleanup restore
11 returned the zone to its original records and preserved nameservers. All restore
jobs retained encrypted safety copies. No public Cloudflare zone was changed.

Independent verification reconfirmed the original zone, authoritative absence of
the test TXT record, completed backup/restore/undo/cleanup jobs, active API/daemon,
OLS and PowerDNS services, and deployed Python/frontend artifacts matching the
worktree/build. Cloudflare-native recovery and mail-routing recovery remain open;
this successful local-provider proof does not establish either capability.

### Mail-routing recovery validation — 2026-09-14

Added a separate mail-routing validator for the existing encrypted mail metadata.
It verifies account identity/current mail-domain ownership, supports domain subsets,
normalizes SQL 0/1 rule status, validates forwarder/catch-all destinations, and
requires automatic-reply mailboxes to exist. Automatic reply subject/body bounds,
header control characters, complete dates and date ordering are checked. Duplicate
rules and incomplete metadata are rejected. The result excludes mailbox passwords,
quotas and domain activation state so routing recovery cannot overwrite them.

Added a private file reader with regular-file, owner, permission, size and symlink
checks. The future coordinator must verify snapshot ownership before decrypting
and passing its fixed metadata path to this reader. No mail delivery, routing SQL
or Sieve script is modified by this validation step.

Mail-routing capture/application coordination, SQL/Sieve recovery, encrypted undo,
queue/UI and live validation remain outstanding. Cloudflare-native DNS recovery
also remains open; work on mail routing does not remove that requirement.

Validation: 12 routing tests passed (21.40s), covering credential exclusion,
invalid addresses/status/content/dates, missing responder mailbox, domain/identity
isolation, subset selection and incomplete metadata. The private reader test also
passed (2.89s), covering credential exclusion and unsafe permissions/symlinks/JSON.
Mail lookups were mocked. This code is not deployed and no live mail settings changed.

### Consistent current mail-routing capture — 2026-09-14

Added current-state capture for routing safety copies. It selects only currently
owned mail domains, reads forwarders/catch-all/automatic replies in one MariaDB
repeatable-read transaction, normalizes status/date fields, and rechecks domain
registration ownership afterward. It never selects mailbox passwords or quotas.
An empty selected subset performs no mail SQL query; foreign selection is rejected
before opening the provider connection. The coordinator still needs to serialize
mail mutations and encrypt this payload before recovery writes.

Validation: the existing 13 routing/reader tests passed (19.73s). Three isolated
MariaDB tests passed (15.82s), including rule preservation, credential exclusion,
SQL query-field inspection, foreign selection rejection and a concurrent catch-all
edit: the first capture retained the original consistent view and the next capture
saw the new value. The fixture used a temporary database/socket with networking
disabled; no production mail database or messages were touched.
The additional real-SQL ownership-transfer test passed (23.39s), confirming that
capture rejects a domain transferred between its ownership lookup and provider
read. This code is not deployed. Routing SQL/Sieve application, encrypted undo and
queue/UI integration remain incomplete, alongside Cloudflare-native DNS recovery.

### Transactional mail-routing SQL replacement — 2026-09-14

Added an internal SQL replacement primitive for selected owned mail domains. It
resolves and locks every selected domain and responder mailbox before writes,
rechecks account/domain bindings, and replaces forwarders, catch-all and automatic
reply records in one transaction. Errors roll back all changes. Mailbox password,
quota and activation records are untouched. This primitive is not exposed as a
restore action: the coordinator must first provide mutation locking, encrypted
previous state, and coordinated Sieve script recovery.

Validation: 13 routing/reader tests passed (22.01s), and all six isolated real
MariaDB tests passed (22.56s). New cases verify replacement and round-trip recovery,
unchanged mailbox credentials and unrelated accounts, and rollback after forwarder
deletion when the next table operation fails. The database fixture used a private
temporary socket with networking disabled. No production mail settings changed;
this code is not deployed. Sieve recovery, encrypted undo, queue/UI integration,
and live validation remain outstanding, as does Cloudflare-native DNS recovery.

The requested phpMyAdmin retry also succeeded: DNS resolved to 104.234.179.66 and
verified HTTPS returned a 302 redirect to /boron_signon.php at 05:19 UTC.


### Mail mutation and backup capture coordination — 2026-09-14

Direct mail-domain/mailbox mutations, forwarding/catch-all/automatic-reply edits,
Sieve application/removal, and local/remote email routing mode changes now use
Boron’s existing cross-process SQL mutation lock. Handler scope covers SQL plus
cache/filesystem changes; lower-level mutation helpers also guard direct callers.
Nested helpers are reentrant. Interactive competing writes fail before side
effects; background metadata capture and routing replacement wait for the lock.
This shares the established lock ordering with mailbox provisioning and account
imports instead of introducing a second independent mail lock.

The mail backup source coordinator now acquires the lock before loading current
account/mail-domain registrations and retains it through provider capture and
private metadata writing. The manifest and selected mail roots use that refreshed
registration list. Tests isolate the private lock directory even when no test
control-plane database is required.

Mail-routing recovery is still not exposed or deployed. The future coordinator
must hold account and SQL locks across its safety capture, encrypted backup and
SQL/Sieve application. Sieve recovery, encrypted undo, queue/UI and live proof
remain outstanding, alongside Cloudflare-native DNS recovery.

Next Sieve recovery work must preserve the actual previous active script (including
an existing custom script or absence), rather than assuming SQL responder records
can reconstruct it. Validate/compile all desired scripts before live writes, reject
unsafe mailbox/script paths, retain encrypted prior script state for undo and
interruption recovery, and coordinate activation with the selected routing SQL.

Validation: 115 mail/coordination regressions passed (257.41s): competing mutation
rejection before side effects, nested helper reentrancy, waiting capture workers,
cross-process SQL locking, routine mail provisioning/forwarding/catch-all/replies,
local/remote routing, real Sieve compilation, routing validation/private reads,
real isolated SQL rollback and capture, and encrypted mail metadata/message backup
and restoration. Another 14 source/job tests passed (65.14s), including ownership
transfer while the source worker waits, private metadata permissions, and existing
backup job/API scope behavior. A pre-existing Starlette TestClient deprecation
warning was reported. All SQL fixtures used temporary sockets with networking
disabled; no live mail configuration or production database was modified.


### Exact Sieve safety capture and reply preparation — 2026-09-14

Added snapshot_mail_sieve for automatic-reply recovery. Current scripts are
captured as exact bytes (including custom scripts and non-UTF8 comments), with
explicit absence distinguished from an empty script. Current account/domain and
mailbox ownership is checked before storage access and again afterward. Traversal
uses directory descriptors with no-follow opens; unsafe owners/permissions,
symlinks, hard links, special files, oversized content and scripts replaced or
modified during capture are rejected. Missing lazy mailbox homes are recorded as
script absence without creating directories. Derived compiled caches are left
untouched. The result must remain private and be encrypted by the coordinator.

Preparation validates matching selected routing domains, covers the union of
saved/current panel responders, compiles enabled restored replies with the real
Sieve compiler, and represents disabled/removed replies as script absence. Custom
scripts outside the affected panel responder set are left alone. Compiler errors
are sanitized. Preparation does not activate scripts or change SQL. The shared
SQL lock covers these operations; a future coordinator must retain it through
safety capture, encryption and activation, together with the account lock.

Still required: guarded script activation and failure/interruption handling,
encrypted SQL/script undo, queue/UI integration and live proof. This development
code is not deployed. Cloudflare-native DNS recovery remains open as well.

Validation: the initial 19 capture tests passed (31.15s). The expanded capture and
real-compiler preparation suite passed all 24 tests (40.96s), plus the invalid
Unicode encoding test passed (4.95s). Cases cover exact custom bytes and explicit
absence, no lazy-home creation, unsafe storage variants, invalid/foreign mailbox
selection, ownership transfer, concurrent script replacement, size limits, enabled
reply compilation, disabled/removed replies, unaffected custom scripts and
sanitized compiler failures. The last two runs reported pytest cleanup warnings
for older privileged temporary fixtures; their test cases passed. No live script,
mailbox or routing record was changed.


### Guarded Sieve activation primitive — 2026-09-14

Added internal script activation with account-bound private-document validation,
strict bounded base64 decoding, exact desired/safety/guard selection matching,
current mailbox authorization and owned durable mailbox guards. Every desired
script is compiled and every current script/cache is preflighted before the first
live write. Script state is checked again immediately before activation. File
replacement uses exclusive temporary files, vmail ownership, mode 0600, fsync and
atomic rename; stale compiled caches are removed. Lazy mailbox homes are created
with vmail ownership and mode 0700 only when a script must be installed. Absent
scripts remain distinct from empty scripts. Readback precedes the caller’s
per-mailbox durable checkpoint callback.

The primitive retains guards on success and failure. It does not claim batch
atomicity or automatically replay after a partial/uncertain write. Its recovery
coordinator must first persist encrypted SQL/script safety state and guard tokens,
hold the account/SQL locks and quiesce delivery; a guard alone does not drain
existing sessions. The coordinator must inspect and reconcile interrupted work,
verify the complete result and record release intent before releasing guards.

This internal code is not exposed or deployed. Durable routing/Sieve coordination,
encrypted undo, queue/UI integration, live proof and Cloudflare-native DNS recovery
remain open.

Validation: 39 Sieve capture/preparation/activation tests passed (89.66s), using
temporary mailbox/guard directories, actual vmail UID/GID 150, real guard tokens
and the real Sieve compiler. New cases verify exact non-UTF8 custom-script undo,
file ownership/modes and cache removal, lazy-home creation and undo to absence,
whole-batch compile failure before writes, wrong guard rejection, stale safety
state rejection, partial-batch recovery from observed actual state, failed-rename
temporary cleanup, checkpoint failure with guards retained, unsafe compiled-cache
rejection, and invalid account/encoding/duplicate/selection/guard documents. All
existing Sieve capture/preparation tests also passed; no live mail was modified.


### Combined encrypted routing/script recovery bundles — 2026-09-14

Added snapshot_mail_routing_recovery to prepare a selected routing change and
capture its prior SQL rules plus exact affected Sieve scripts under the shared
SQL lock. Prior routing responders must have matching script safety entries;
extra affected scripts must belong to the selected owned domains. Credential and
unknown script fields are discarded. Forwarding-only operations can carry an
explicit empty script set without broadening script activation’s nonempty guard
requirement.

Safety sources bind account ID, username and restore job, use a fixed private
filename, exclusive creation, mode 0600 and file/directory fsync, and are encrypted
with restic before returning the snapshot identifier. Failed or uncertain storage
operations retain the private source; repeating creation cannot overwrite it.
The coordinator must durably record the snapshot identifier before live changes.

Safety loading checks encrypted snapshot account ownership and exact job-bound
source paths before decrypting, validates private file type/owner/permissions/size
and envelope identity, revalidates current routing/script ownership, and removes
its temporary decrypted tree. A separate loader reads routing from existing mail
backup metadata while excluding mailbox credentials. These are internal helpers;
no API exposes private rules or script content.

Not deployed: durable guarded SQL/Sieve orchestration, supervision/interruption
recovery, encrypted undo queue/UI integration and live proof remain necessary.
Cloudflare-native DNS recovery also remains outstanding.

Validation: five real SQL/restic tests passed (61.78s), plus the forwarding-only
case passed (28.62s). The combined safety copy decrypts exactly after deleting its
private source, rejects overwrite of an existing job source, retains 0600 mode,
excludes fixture mailbox passwords/hashes, and cleans temporary decrypted files.
Foreign-account and wrong-job requests fail before decryption. Existing mail
backup routing loads without exposing credentials. Simulated encryption failure
retains the private source and leaves SQL/scripts unchanged. Incomplete responder
safety is rejected before storage. Forwarding-only safety round-trips with empty
script sets and preserves unrelated custom scripts. Fixtures used private MariaDB
sockets with networking disabled and temporary restic repositories; no live mail
state was modified.


### Durable routing journal and offline worker — 2026-09-14

Added an account/job/path-bound private routing journal. Creation decrypts and
compares the encrypted safety copy before publishing a journal containing planned
guard tokens, previous/desired rules and scripts, and a unique supervised operation
identifier. Guard acquisition persists intent before publication and can resume
using only those same tokens after lost acknowledgement. It never adopts another
job’s guard.

The offline worker requires complete mail-service shutdown, checks actual prior
SQL/script state, persists intent before SQL replacement, checkpoints SQL commit
and each verified script activation, and verifies the combined result. Any phase
past initial guarding rejects blind execution replay. Inspection compares actual
SQL and scripts with both desired and prior state, regardless of stale checkpoint
claims. Guards remain owned on completion or failure; this is not panel-job
finalization.

The launcher uses the existing systemd supervisor and only a fixed internal Python
module, bound account ID and private journal path. SQL coordination is released
before launching the independent worker; the offline worker rejects lock
contention promptly instead of waiting with Dovecot stopped. Recovery checks the
same supervised operation before inspecting state and returns waiting while that
worker is confirmed running. The command-line worker prints only a generic failure
message, keeping private script content and guard tokens out of service logs.

Remaining integration includes recovery decisions/rollback after partial writes,
durable release intent/finalization, UI/queue and encrypted undo wiring, and live
supervised validation. Direct mail mutations between worker phases also require
an explicit audit when connecting this to active restore jobs. No routing code
from this development sequence has been deployed. Cloudflare-native DNS recovery
and the final broad backup audit remain open.

Validation: seven journal tests passed (74.97s), plus three supervisor-integration
tests passed (31.29s). Real isolated MariaDB, temporary vmail scripts/guard files
and a real encrypted safety-copy test verify SQL/Sieve execution, retained guards,
readback, replay rejection, lost SQL checkpoint classification, resumable guard
publication, changed-state rejection, account/path isolation and encrypted-source
matching. Integration tests verify fixed launcher arguments, release of the SQL
lock before child launch, prompt offline lock-contention rejection, and no state
inspection while the supervisor reports the worker running. Service shutdown and
supervisor execution were mocked; no live service was stopped. These results do
not establish live systemd orchestration or complete rollback/finalization.


### Routing finalization and durable guard release — 2026-09-14

Added finalization for verified routing operations under SQL coordination and
caller-held account/repository locks. It checks the existing supervisor operation,
waits while that worker is running, requires the mail service to have resumed,
decrypts and matches the previous-state safety bundle, and verifies actual desired
SQL/script state. It then verifies guard ownership and durably records release
intent before invoking batch guard release. Completion is persisted afterward.

An interrupted release resumes from its durable intent, accepts already removed
markers, and verifies every remaining token before removal. A newer job’s guard
is never adopted or released. Changed actual state requires recovery inspection;
finalization does not overwrite later edits. Completed journals are idempotent and
recovery does not inspect a supervisor unit that may have been reused by a later
operation. Release-intent recovery routes to finalization rather than ordinary
inspection, which requires every guard still to exist.

Still not deployed: partial-application rollback/recovery decisions, queue/UI and
undo integration, direct-mutation coordination across worker phases, and live
supervised proof remain open. Cloudflare-native DNS recovery and the full final
backup audit remain required before the initial goal is complete.

Validation: seven finalization tests passed (74.47s), plus two release/recovery
cases passed (32.30s). They cover durable intent preceding release, idempotent
completion, running-worker/decryption exclusion, stopped-service refusal,
mismatched encrypted-state refusal, lost release acknowledgement, lost completion
checkpoint, changed-state refusal, protection of a later job’s guard, and recovery
after supervisor reuse. SQL and guard files were real isolated fixtures; supervisor
and mail-service status plus safety-loader prerequisites were mocked here (real
encrypted safety loading is covered by the preceding storage/journal tests). No
live mail service or production mailbox was changed.


### Encrypted rollback of interrupted routing application — 2026-09-14

Added an explicit reverse operation for a stopped, incompletely finalized routing
restore. Preparation first confirms the original worker is no longer running and
decrypts/matches the original safety target. Under the existing guard tokens it
accepts only SQL state matching the original or intended transaction and script
bytes individually matching original or desired content. Unrelated later changes
are refused. Original target scripts are compiled before reverse authorization.

The actual partial state receives a separate immutable encrypted rollback safety
copy, with a purpose-bound filename and envelope. Its decrypted content is checked
before writing a fresh rollback journal with a new supervised operation ID and
unchanged job/guard ownership. The reverse journal uses the existing offline
application and finalization pipeline. Both original and rollback safety copies
remain available. Journal direction selects fixed allowed paths; purpose checks
prevent loading a rollback snapshot as an original safety snapshot.

Once a reverse journal exists, the original operation cannot execute or finalize.
Recovery follows the reverse journal and reports rolled_back explicitly after
completion. The rollback launcher retires only the observed terminal original
supervisor operation before creating the next supervised worker. Original journal
history is retained unchanged.

Still required before release: queue/UI integration and undo controls, persistent
protection of both safety snapshots in retention, direct mutation exclusion across
worker phases, recovery handling if rollback itself is interrupted, and live
supervised validation. Cloudflare-native DNS recovery and the final broad backup
audit also remain open. These changes are not deployed.

Validation: 13 rollback/finalization tests passed (180.31s). The real encrypted
round-trip induces a lost SQL checkpoint, saves the partial state separately,
reverses SQL and exact custom script content, finalizes guard release, preserves
the original journal and both encrypted safety snapshots, rejects wrong-purpose
snapshot loading, and reports rolled_back through original-job recovery. Other
cases reject a running worker before decryption, protect unrelated script edits,
and retain current state/guards on rollback encryption failure. Existing nine
finalization/release recovery cases also passed. Fixtures used isolated MariaDB,
temporary vmail/guards and real restic; supervisor and service-state calls were
mocked. Live service supervision is still unverified for this routing workflow.


### Paired routing safety retention — 2026-09-14

Added immutable per-purpose routing safety checkpoints on running mail_routing
restore jobs. The original copy must be recorded first and remains the primary
undo snapshot; rollback safety is retained separately in the persisted job summary.
A repeated identical checkpoint is allowed, but replacing either purpose’s existing
snapshot is rejected.

Retention now collects every registered primary/original/rollback reference.
Routing jobs must be completed and explicitly routing_finalized before their
copies become eligible. Retention keeps the newest eligible jobs as groups,
protects all copies of unresolved jobs and snapshots used by queued recovery, and
expires only unprotected older references. Repository failure preserves metadata;
a retry can reconcile copies already deleted before a lost acknowledgement.
Invalid routing reference metadata blocks cleanup for inspection. Existing mail
restores still require displaced-data cleanup before becoming eligible.

The pending routing queue worker must call record_routing_safety after each
successful encrypted copy and before live changes, and set routing_finalized only
after durable journal finalization. This retention support is not a claim that the
routing worker/UI integration is complete. Interrupted rollback handling, direct
mutation coordination across phases and live supervision remain open, alongside
Cloudflare-native DNS recovery and the final broad audit. Not deployed.

Validation: eight paired-retention/checkpoint tests passed (18.47s), covering
completed-pair expiry, running/failed/unfinalized protection, queued use of a
secondary copy, immutable purpose recording, invalid-reference rejection, and
repository deletion followed by lost acknowledgement and metadata reconciliation.
Two existing real encrypted file-recovery retention regressions passed (53.08s),
and the mailbox displaced-cleanup retention regression passed (4.26s). New routing
selection tests mocked repository listing/deletion; existing file tests exercised
real restic repositories. No production snapshot or live account was changed.


### Persisted routing recovery excludes ordinary mail mutations — 2026-09-14

Added ordinary-mail mutation coordination that checks persisted routing restore
jobs while holding the existing SQL mutation lock. It resolves ownership through
mail-domain and hosting-domain registrations, explicit account usernames during
provisioning, or account objects during teardown. Positional and keyword calls are
handled. Pending/running routing jobs block edits; failed or unfinalized jobs with
safety state remain protected after their process exits. Finalized rollback can
permit edits while retaining its encrypted safety copies. Failure before any safety
copy does not indefinitely block the account.

Mail provisioning/deletion, forwarding/catch-all/reply changes, password changes,
Sieve application/removal, and email-routing mode changes check before provider,
cache or filesystem side effects. Internal recovery continues through its own
account/guard-authorized SQL/Sieve primitives, not an editable bypass flag.
Suspension and termination also check before locking the system user, invoking
teardown hooks or changing account status. Read-only mailbox listings and other
accounts remain available. Lookups filter routing jobs within the account scope.

The pending queue integration must use kind=mail_routing, register safety copies
before guard acquisition and persist routing_finalized only after journal release
completion. This closes the ordinary-edit gap for those persisted jobs; it does
not assert that the unintegrated development workflow is already exposed. Queue/UI,
interrupted rollback handling and live supervision remain open, as do Cloudflare
DNS recovery and the final backup audit. Not deployed.

Validation: the mail/coordination/Sieve suite reported 96 passed and one fixture
failure (181.47s); the lifecycle selection reported 33 passed and the same fixture
failure (114.21s). The test's source SnapshotRun was unintentionally pending, so
termination correctly hit the older pending-backup guard before the new failed
routing-recovery check. The fixture was corrected to mark that backup completed.
All 16 persisted-recovery tests then passed (51.55s), including termination before
status/system changes. No other regression failed. These cases cover absent-worker
protection, positional/keyword calls, lazy-domain ownership, provisioning username
scope, another account/read-only access, finalized recovery, and pre-safety failure.
System-account operations and mail providers were mocked in lifecycle/handler
checks; Sieve compiler regressions used the local compiler. Test control-plane
lookups are isolated, including previously filesystem-only autoresponder tests.
No production account or mail settings changed.


### Account-scoped mail-routing recovery preview — 2026-09-14

Added a mail-routing catalog for existing encrypted mail backup metadata. It
checks snapshot ownership before decryption, validates the private document once,
and reports each saved domain independently as available or unavailable according
to current ownership and responder-mailbox requirements. Public results contain
only domain names, availability/reasons, and forwarding/catch-all/responder counts;
no mailbox credentials, destinations, reply text or script content are returned.
A transferred/unavailable domain does not hide the account’s other valid domains.

The daemon preview uses the owned backup-run check and a nonblocking repository
lock. A backup without the mail component returns an empty preview without
restoring metadata. The account backup API exposes the preview through the same
account-access check as existing restore previews. The daemon registers the new
read operation in its blocking-work pool. Existing routing loading now shares the
bounded private metadata reader with the preview.

This is the preview layer only: restore kind/queue execution and frontend controls
are not yet enabled for mail routing. Worker checkpoint/retention integration,
interrupted rollback recovery, live supervised proof, Cloudflare-native DNS
recovery and the final broad audit remain outstanding. Not deployed.

Validation: five tests passed (72.44s). Real isolated MariaDB and restic fixtures
verify one metadata decryption, counts-only output, credential/content exclusion,
independent transferred-domain unavailability, private staging cleanup, foreign-run
and busy-repository rejection before decryption, no-mail-component handling, and
unchanged owned routing loading. The account API test confirms the new route calls
the intended daemon operation and denies another account before making any RPC.
One existing Starlette TestClient deprecation warning was reported. No live account,
mail service, production repository or routing rule was changed.


### Queued mail-routing restore, undo and supervised recovery — 2026-09-14

Connected mail_routing restore requests to domain availability validation,
username confirmation, explicit mail-pause acknowledgement and mail-component
checks. Undo validates its bound safety copy and inherits the original selection.
Queue insertion rechecks account status, competing jobs and unresolved persisted
routing recovery. The API accepts mail_domains. Restore/undo preflight RPCs use
the separate reporting pool because validation can decrypt metadata.

The queue worker captures/compiles selected routing, encrypts and registers the
original safety copy, creates the bound journal and guard tokens, then releases
SQL coordination before invoking the supervised worker. Successful finalization
persists routing_finalized and retains registered safety references. Private
journals are retained instead of being removed by generic file-restore cleanup.
Undo merges exact saved scripts with removal of panel replies added afterward and
captures its own previous state before applying.

Startup and exception recovery inspect the same journal/supervisor operation.
Confirmed-running workers are reobserved through one delayed callback per job.
Failures before guard acquisition close safely; interrupted original application
can prepare, register, apply and finalize its encrypted rollback. A successful
rollback reports the requested restore as failed-but-recovered, with rolled_back,
routing_finalized and released guards, while preserving both safety IDs. Such
resolved rollback pairs may later expire under policy; unresolved failures remain
protected. Unknown/partial rollback outcomes retain recovery data and fail for
inspection rather than replaying mutations.

Still required: recovery continuation when rollback itself is interrupted,
frontend routing restore/undo controls and history, final integration audits and
live systemd supervision/proof before deployment. Cloudflare-native DNS recovery
and the remaining initial-goal final audit remain open. No new routing workflow
code is deployed yet.

Validation: the initial queue run had three passes and one assertion failure
(124.11s); automatic rollback similarly reached its final assertions before a
field-name error (82.54s). Both tests incorrectly used raw SQL source_local_part
instead of public list_forwards local_part. After correcting the tests, queued
restore/exact-script undo plus API authorization passed (2 tests, 95.11s), automatic
partial-failure rollback passed (74.52s), and supervisor observer-timeout recovery
passed (53.86s) without a second launch. Together with the initial three passes,
all six queue scenarios and the API check are verified. Nine paired-retention
checks passed (37.15s), including expiry of a finalized rollback. The API test
reported the existing Starlette TestClient deprecation warning.

Queue tests use real isolated MariaDB, real restic encryption and temporary
vmail-owned files/guards; supervisor execution/service status are mocked, with the
real internal journal worker invoked by the fixture. They verify persisted safety
IDs before mutation, pending-job edit exclusion, selection/account rejection,
pre-guard failure closure, startup recovery dispatch, retained private journals,
exact custom-script undo, both registered rollback safety copies, and final mail
edit availability. No production account, service or repository was changed.

### Email settings recovery interface — 2026-09-14

Added lazy-loaded domain selection for saved forwarders, catch-all settings and
automatic replies. The picker shows counts and per-domain unavailability, supports
case-insensitive filtering and selecting visible available domains, and requires
account-name confirmation plus an explicit server mail-pause acknowledgement.
It sends mail_routing with mail_domains to the queued restore API. Recovery-point
component labels now describe email messages and settings.

Restore history labels routing operations as Email settings and supplies the
matching undo description and pause acknowledgement. Only finalized successful
routing restores offer undo. Failed automatic rollbacks explain that previous
settings were recovered; unresolved routing failures retain recovery data and
request administrator inspection instead of suggesting an unsafe repeat action.

Validation: production frontend build passed (46.81s), git diff --check passed.
Four existing file restore/undo browser cases passed. Four new routing browser
cases passed after correcting selectors to target visible mobile elements (the
responsive table renders both desktop and mobile markup). New cases cover both
skins in light/dark modes, lazy catalog loading, unavailable domains, case-insensitive
selection, mandatory acknowledgement, exact restore/undo request payloads,
failed-versus-recovered history and mobile overflow. Browser APIs are fixtures;
these checks do not replace backend/live supervision verification. Inspected the
Evolution mobile recovery dialog screenshot. No production deployment performed.

Remaining: interrupted rollback continuation, live supervised mail-routing proof,
Cloudflare-native DNS recovery, full initial requirement/release audit, followed
by the separately queued product expansion. Goal remains active.

### Interrupted mail-routing rollback continuation — 2026-09-14

Added continuation for a rollback interrupted before its SQL checkpoint, between
SQL and script activation, or after script activation before verification. The
coordinator first observes the existing worker and waits while it is running;
only an exited/missing worker permits continuation. It decrypts and compares the
same rollback safety copy, validates owned guard tokens, and accepts only SQL
matching either saved endpoint and individual scripts matching either endpoint.
Unrelated changes fail closed. The offline worker repeats these checks with the
mail service stopped and SQL coordination held, applies against the actual script
bytes, checkpoints again, and finalizes through the existing guard-release path.
Safety IDs and encrypted previous data remain unchanged.

The launcher retires only the observed operation unit. A prepared-but-unlaunched
reverse journal may recognize its bound original operation's terminal unit; a
partial reverse journal cannot fall back to that original identity. Queued startup
recovery now dispatches partial/prelaunch rollback to this continuation instead
of immediately requiring inspection. Repeated application failures still retain
recovery state and guards for inspection; no blind retry loop was added.

Validation: initial rollback/journal regression passed 17 tests in 311.47s.
After adding final worker identity handling, the full rollback suite plus queued
process-interruption recovery passed 10 tests in 298.52s. The queue test interrupts
both original and reverse SQL checkpoints, then verifies startup continuation,
failed-but-recovered status, unchanged paired safety IDs, exact original custom
script bytes, restored rules and ordinary mail-edit availability. Additional cases
cover still-running-worker waiting, same-operation retirement/relaunch, unrelated
script rejection, original journal replay rejection and guard ownership. Tests
use isolated MariaDB, restic and temporary vmail files. Supervision/service status
are mocked; no production mail service or account was changed. git diff --check
passed. This change is not deployed.

Remaining initial work: real supervised routing proof and deployment, final
recovery integration/audit, Cloudflare-native DNS recovery, full requirement and
release/self-update audit. The queued expansion remains after the initial goal.

### Real systemd mail-routing proof — 2026-09-14

Added and passed two real supervisor tests (122.67s). Independent Python workers
use isolated SQLite, MariaDB, vmail files and guard paths plus a disposable
systemd service. The real service barrier, supervisor and routing SQL/Sieve worker
are exercised without mocking service state. Successful application changes actual
rules/scripts and finalizes. The interruption case SIGKILLs the original worker
after script activation but before verification, prepares encrypted rollback,
SIGKILLs the reverse worker after SQL commit, then continues the same reverse
operation and verifies exact original script bytes and rules. Each worker run
proves service restart with a new PID; guards remain until final verification.
The original and rollback safety copies remain bound and retained. Temporary unit
cleanup belongs to the fixture. Production Dovecot and hosting data were untouched.

This proves real systemd supervision with isolated data; actual panel deployment
and live account/API routing recovery verification are the next steps. Remaining
Cloudflare recovery and full-goal/release audits are unchanged.

### Routing recovery deployment and live catalog — 2026-09-14

Deployed runtime commit fd4c4bc using the existing protected deployment script.
Idle WordPress/backup-job checks and mail guard verification passed. Private
rollback copy: /root/boron-setup/mail-recovery-before-20260914-064619.
Log: /root/boron-setup/routing-recovery-deploy.log. The script stopped only panel
API/daemon, retained configuration bytes, applied additive schema initialization,
restarted services and verified HTTPS/admin login/configuration RPC on port 2222.

Read-only live proof /root/boron-setup/routing-recovery-deploy-proof.json confirms
that retained wpdevqa mail snapshot run 3 exposes one available routing domain
through authenticated HTTPS and returns only catalog fields. API, daemon, OLS,
PowerDNS, Dovecot and chrony are active. Deployed routing worker/journal and built
frontend index exactly match the checkout. No live routing restore was triggered
by this catalog check. Actual QA-account routing restore/undo remains to be proven,
followed by Cloudflare-native DNS recovery and full initial-goal/release audit.

### Live email settings restore/undo and cleanup — 2026-09-14

Verified the deployed workflow through authenticated HTTPS using isolated account
pq0914020151 and its own primary domain. It previously had no mail domain or
mailboxes. Provisioned its mail domain, created one randomly named QA mailbox,
forwarder/catch-all and a future-dated automatic reply, then created manual
mail-only policy 4 against the existing QA destination 3, with no notifications.
The future date prevents the test reply from sending during this proof.

Backup run 8 captured the baseline. After modifying forwarders, removing catch-all
and changing the reply, live restore 12 recovered exact baseline rules and Sieve
bytes. Undo 13 recovered the exact post-backup rules/script. Both operations were
completed by the real daemon/systemd/Dovecot workflow and retained encrypted safety
copies. Mailbox password/quota/active-state fingerprint stayed unchanged. Both
records report routing_finalized and guards_released.

Removed only the generated QA mailbox, reply and named test forwarders; restored
the initially empty routing state and disabled policy 4. The newly provisioned QA
mail domain remains for further testing. No live mail guards remain. API, daemon,
Dovecot, OLS, PowerDNS and chrony are active after cleanup. Other customer domains
and mailbox settings were not selected by either recovery.

Proof script/log/state: /root/boron-setup/routing-recovery-live-proof.py,
/root/boron-setup/routing-recovery-live-proof.log and
/root/boron-setup/routing-recovery-live-proof.json. State is verified with
initial_routing_restored, test_mailbox_removed and policy_disabled all true.
Do not rerun this script against its existing state file; IDs are historical proof.

Mail routing recovery now has deployed UI, backend, isolated interruption tests,
real systemd tests and live account restore/undo evidence. Remaining initial work
includes Cloudflare-native DNS recovery, the final broad backup/requirement audit,
and GitHub/release/self-update verification. The subsequent product expansion is
still queued after the initial goal; the overall goal remains active.

### Cloudflare recovery batch transport — 2026-09-14

Reviewed current Cloudflare batch documentation and recorded provider constraints
and implementation gates in docs/CLOUDFLARE-RECOVERY-PLAN.md. Added a dedicated
one-attempt batch transport instead of reusing the general retrying request
helper. It requires a bound 32-hex zone ID, validates operation collections/current
record IDs, forbids saved IDs on new records, and caps batches at 200 operations.
It retains native fields and scoped token context. Timeouts, rate-limit/server
errors and malformed success envelopes require inspection rather than automatic
POST retry. This helper is internal; native validation and current-ID planning
remain the recovery coordinator's responsibility.

Validation: 50 tests passed in 61.41s, covering new transport behavior and the
existing Cloudflare client. New fixtures verify one request, bound token/path,
Auto TTL/proxy/settings/comment/tag retention, invalid batch rejection before
transport, and no repeat on timeout/429/500/503 or malformed responses. No external
DNS API was mutated. Cloudflare restore remains disabled and this new helper is
not deployed. Native validation, batch planning/checkpoints, restore/undo/UI
integration and final verification remain required. Overall goal stays active.

### Native Cloudflare record validation — 2026-09-14

Added an independent recovery record normalizer with no network writes. It checks
zone containment, DNS content parsing, native TTL/proxy semantics, comments/tags,
known settings and private routing. Saved IDs are discarded; requesting a current
ID requires a valid 32-hex identifier. Read-only provider metadata is excluded.
Locked/provider-managed records and unsupported fields/types fail explicitly for
later dedicated handling. Initial types cover A/AAAA/CNAME/MX/NS/PTR/TXT/OPENPGPKEY
and structured CAA/SRV. Cloudflare apex CNAME is permitted, self-CNAME rejected.
Structured data and redundant textual content must agree. Caller account/provider
binding validation and authority-record protection remain separate requirements.

Validation: 52 tests passed across this validator and the one-attempt batch
transport. Cases include native option retention, account-zone boundaries, bool
versus integer validation, malformed records/settings/tags, inconsistent structured
content, current-versus-saved IDs and proxied automatic TTL. No public DNS calls,
record changes or deployment were performed. Cloudflare restore remains disabled.
Remaining native work includes other structured types/legacy schema variants,
provider-managed record handling and semantic collection/diff planning before
batch execution, checkpoint/undo and availability integration.

Schema reference: https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/create/

### Cloudflare current-record recovery planning — 2026-09-14

Added a pure planner that validates complete writable record collections, matches
unchanged native records without saved-ID authority, and produces current-ID PUTs,
DELETEs and ID-free POSTs. Same-value records are matched before other updates so
TTL/comment/proxy changes do not unnecessarily swap addresses. Matching uses
indexed queues rather than quadratic scans. Equivalent IPv6 spellings normalize
to the same address; disabled optional settings and read-only IDs do not create
false differences. Counts preserve duplicate records for verification.

Plans are deterministic and split at 200 operations. All deletions precede
updates/creates across batch boundaries, including large address-set-to-CNAME
transitions. Duplicate current IDs, foreign record names, CNAME/address conflicts,
NS coexistence and managed records fail before a plan is returned. The caller must
still partition protected/provider-managed records, validate ownership, encrypt
previous state, recheck current state and checkpoint/verify each submitted batch.
No new planner path is exposed to users yet.

Validation: 62 tests passed in 1.36s across planner, native validator and batch
transport. New cases cover unchanged/current-versus-saved IDs, native metadata,
same-address matching, a 405-record replacement across three batches, simulated
application yielding the desired state, duplicate count comparison, equivalent
IPv6 values and invalid collection rejection. git diff --check passed. No public
DNS writes or deployment occurred. Cloudflare restore stays disabled pending
protected records/additional native schema support and execution/undo integration;
full goal and subsequent expansion remain active.

### Cloudflare execution and encrypted restore/undo integration — 2026-09-14

Connected native Cloudflare validation/application to snapshot DNS configuration
recovery and its existing encrypted previous-configuration/undo path. The catalog
now marks supported Cloudflare zones available in development. Provider bindings
and scoped token context come from the current account registration; saved IDs
are never mutation authority. The whole previous configuration is encrypted before
any selected zone is changed. Batch progress is persisted through the restore
worker's update callback.

The new executor partitions and retains authority/provider-managed records,
rejects overlaps with managed owners, and reads current records again before each
batch. It checks both complete values and ID-to-value associations plus protected
records. Each batch is sent once; even an error/timeout is followed by observation,
and execution continues only if the complete expected result is confirmed. Unknown
or uncommitted results stop with previous configuration retained for explicit undo.
Newly created IDs come from provider rereads. Final verification compares the
complete desired writable state. No automatic mutation replay was introduced.

Validation: 101 integration/regression tests passed in 248.57s, including real
restic-encrypted Cloudflare backup/restore/undo against a simulated provider,
counts-only catalog, scoped token use, protected records, committed timeout without
replay, failed pre-commit write, 405-record multi-batch changes, changed record ID
associations, provider ownership callback rejection, native/planner/transport
checks and existing local PowerDNS restore/undo regressions. Eight executor tests
also passed after adding later-batch failure coverage: only the first verified
batch is checkpointed and the second failure stops further requests. No production
DNS record was changed and this code is not deployed.

Remaining: additional native schema/legacy variants, broader UI/release and
suitable live-provider verification, followed by the full initial requirement
and GitHub/self-update audit. The separate expansion backlog is preserved.

### Structured DNS records and Cloudflare recovery UI — 2026-09-14

Added native HTTPS/SVCB/TLSA/DS validation and canonical text comparison. HTTPS/SVCB
parameters normalize through the DNS parser, so equivalent parameter ordering
does not cause false recovery differences. TLSA/DS integer and hexadecimal fields
are bounded and preserve native data. The first schema run found SVCB targets
were parsed relative to the zone after stripping the trailing dot; fixed the
renderer to use absolute target names. Cloudflare's current delegation docs also
confirm NS and DS coexistence for secure child delegation, now explicitly allowed
by the planner and tested.

The configuration recovery picker labels Cloudflare records separately from local
record sets and explains propagation delay. Submission now checks that every
selected zone remains available in the current catalog. Existing confirmation,
selection reset and undo behavior are retained.

Validation: final native/planner/executor/transport run passed 80 tests in 4.89s.
Production frontend build passed in 45.80s. Eighteen browser checks passed in 3.0m:
local and Cloudflare DNS restore/undo in both skins/light-dark modes, mobile
interaction/overflow, unavailable zones and existing PHP/cron recovery controls.
Browser API calls are fixtures. git diff --check passed. No public DNS changes or
new deployment were performed. Remaining native schema variants and release/live
provider verification remain open; this is not completion of the full goal.

References:
- https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/create/
- https://developers.cloudflare.com/dns/manage-dns-records/how-to/subdomains-outside-cloudflare/
