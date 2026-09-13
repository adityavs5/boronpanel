# Active product expansion goal

The complete user objective remains active. A checked item requires implementation and verification, including live-server evidence where relevant. No item is complete solely because its code exists.

- [x] WordPress: explicit scan/import and refresh; soft deletion of records with suppression until manual rediscovery; hard deletion of selected installation files and owned database with confirmation and tenant isolation.
- [ ] Backups: usable admin and customer backup system, reusable jobs, incremental snapshots, inclusion/exclusion filters, notification channels, SSH destinations, retention and restores. Verify restore contents and unchanged-file deduplication, not merely successful commands.
- [ ] Domains/subdomains: independent site roots/public_html, DNS records, consistent creation workflow and ownership.
- [x] Mail: fix missing mail-domain provisioning and verify mailbox creation.
- [x] WordPress URLs: functional http/https and www/non-www installation selection, login and clone compatibility.
- [x] Applications: separate Python App and Node.js App navigation and user workflows.
- [ ] phpMyAdmin: provision and verify real database-scoped access.
- [ ] Panel SSL: issue and serve a valid certificate for the requested panel hostname, with renewal.
- [ ] Panel ports: default shared admin/customer port 2222; admin configuration supports changing both ports, preserving access and enforcing intended role behavior.
- [ ] Direct interactions: database and SSL names/actions first, then audit other comparable lists; accessible desktop/mobile management views.
- [ ] Terminal: configurable BORON ASCII welcome from admin configuration, suppress default Ubuntu status/MOTD in admin terminal; preserve usable prompts and appropriate customer behavior.
- [ ] PHP: account default version inherited by new sites; per-site override dropdown; Lite/Moderate/Max limit presets and editable Custom selected by default.
- [ ] Typography: improve font and dashboard icon-label sizing with local assets and no performance regression.
- [ ] Security/time: admin/customer 2FA setup, recovery, reliable clock synchronization, drift/unsynchronized state detection and actionable diagnostics. Do not claim absolute immunity to host/network failure.
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
