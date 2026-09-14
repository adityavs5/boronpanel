# Incremental backup implementation

## Storage foundation

`daemon/snapshot_storage.py` implements encrypted restic repositories on local disks and SSH/SFTP destinations. It backs up ordinary files directly so unchanged file contents can be reused between snapshots. Each snapshot is independently restorable. A full scan forces file reads while retaining deduplication; it does not create a second compressed account archive.

Snapshots carry both a repository namespace and an account identifier. Listing, browsing, restore and retention enforce that ownership. Retention validates every requested snapshot before deleting any. Pruning preserves chunks referenced by other accounts. Restore writes to an empty private staging directory and verifies restored contents; applying those contents to a running account remains a separate operation.

SSH connections require a pinned host key and a dedicated private key. They disable ambient SSH configuration and agent credentials. Encryption passwords are read from private files, never command arguments. Local repository data, caches and credential files are excluded from backups. Restic runs with reduced CPU and I/O priority and a bounded Go thread setting.

## Verification

`tests/test_snapshot_storage.py` uses real restic repositories. SSH tests start a disposable loopback SSH server with isolated keys, without changing the server's normal SSH configuration. Tests cover:

- Byte-for-byte file recovery and single-file recovery on local and SSH storage.
- Unchanged-file reuse and a small changed-file incremental snapshot.
- Full rescans with reused data, exclusion filters and symbolic links.
- Account and repository namespace isolation, wrong encryption keys and unknown SSH host keys.
- Retention with shared chunks still needed by another account.
- Exclusion of repository contents, caches and encryption credentials.
- Literal special-character filenames and selected-directory restores.

The integration log is `/root/boron-setup/snapshot-storage-tests.log`. Real storage tests require restic; SSH tests also require root and sshd. A skipped integration test is not proof that its workflow works.

## Remaining product integration

The storage and persistent job APIs do **not** yet complete the requested backup product. The service described below now provides destinations, policies, filters, raw database dumps, scheduling, recovery, retention, notifications and account-scoped browsing. Remaining work includes database, mail and broader account configuration restore application, bounded retention for pre-restore recovery points, and live deployment and verification.

The service resolves source paths from account ownership rather than accepting arbitrary customer paths. Account and repository locks coordinate its workers, including retention/pruning. Account metadata and database dumps use stable private staging paths so incremental snapshots can reuse unchanged data. Snapshot and legacy archive backup/restore queues now coordinate account activity. Other account-changing operations still need a final audit before deployment. Existing archive backups remain available during this integration.

References: [restic repository setup](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html), [backup and filtering](https://restic.readthedocs.io/en/stable/040_backup.html), [restore](https://restic.readthedocs.io/en/stable/050_restore.html).

Latest integration run: **9 passed in 76.07 seconds**, including both local and SSH restores and literal special-character path selection. This validates the storage adapter, not the unfinished job/UI integration.

## Persistent job service

`daemon/snapshot_jobs.py` adds destinations, policies and per-account runs in separate SQLite tables. Secrets stay in the private repository directory, outside the API-readable control database. Administrators create a destination, install its generated SSH public key remotely when applicable, supply a trusted SSH server host key, and initialize the repository. The explicit recovery-key endpoint is admin-only and returns `Cache-Control: no-store`.

Policies select accounts (an empty selection includes all active accounts), excluded accounts, files/databases/mail/config, home-relative included paths, exclusion patterns, full/incremental mode, retention count, and email/webhook channels. Each queued run freezes those options. Hourly/daily/weekly schedules use the existing hourly scheduler; manual jobs run on demand. Repeated scheduling and busy accounts do not enqueue duplicate account work.

Workers export raw SQL with a consistent transaction and stable private paths, then snapshot raw home/mail files and metadata. Account and repository file locks coordinate daemon and scheduled processes. Startup requeues pending work and identifies interrupted running work by checking its account lock. Retention marks old history entries expired. Notifications report dispatch or suppression; SMTP acceptance/webhook queueing is not proof of delivery.

Admin APIs are under `/api/v1/backups/snapshots`; customer history and browsing are under `/api/v1/accounts/{username}/backups/snapshots/runs`. Customer history omits global policy account lists and notification selections. The management UI and file restore application are implemented below. Database/mail/configuration restore, broader metadata recovery and live deployment remain unfinished.

Persistent job verification: **69 passed** across the new job tests, existing archive backup tests and RPC tests. After the duplicate-worker notification fix, **12 job tests passed** again. Logs: `/root/boron-setup/snapshot-job-tests.log` and `/root/boron-setup/snapshot-job-final-tests.log`. The only warning was the existing FastAPI/Starlette TestClient dependency deprecation.

## Management screens

The admin Backup Manager at `/app/backup-jobs` provides destination setup (local or SSH), public-key copying, explicit recovery-key reveal/download, job creation/editing, all/selected/excluded account filters, component and path filters, schedule/mode/retention selection, selected notification channels, manual runs and run history. Jobs and destinations have visible clickable names and management controls. Both dashboard theme grids and fuzzy search include the new page.

Customers see scheduled recovery points above their existing on-demand archive backups. Shared details show status, processed files, stored data, components, notification dispatch results and account-scoped browsing. Root shortcuts identify account files, email and database/configuration exports. Long forms keep their action buttons visible while their body scrolls. Admin and customer routes remain role-scoped.

The new admin page is loaded on demand (5.79 kB gzip); shared history is 2.46 kB gzip in this build. No new fonts, image assets or frontend packages were added. These sizes are build evidence, not a measured end-to-end performance benchmark.

File restore application is implemented below; database/mail/configuration restore remains unfinished. This new backup UI has not yet been deployed to the live panel. Existing archive restore functionality is retained.

UI verification: production build passed; **6 browser tests passed** across admin light/dark themes and customer browsing, followed by **2 final customer tests** that also submitted the existing full archive-backup form. **12 backend tests passed** for the updated history and browsing behavior. Evidence: `/root/boron-setup/backup-ui-build.log`, `backup-ui-browser.log`, `backup-ui-customer-final.log`, `backup-ui-api.log`, and screenshots in `/root/boron-setup/backup-ui-proof`. Browser flows use mocked API data; they are not evidence of live deployment or completed snapshot restore application.

## File restore application and previous-file recovery

`daemon/snapshot_restores.py` queues account-scoped file restores and stages verified snapshot contents before touching the account home. Users select files/folders from the browser or restore all captured account files, then type the account username to confirm. Current versions are saved in a separate encrypted pre-restore snapshot. History exposes recovery of those previous versions. This is not an exact filesystem rollback: files with no prior version are retained, and file/directory type conflicts fail rather than deleting existing directories.

`daemon/snapshot_file_worker.py` opens source and destination directory descriptors and permanently drops to the account UID/GID before writing into its home. It copies through directory descriptors, refuses target directory symlinks, rejects source links escaping the home, uses atomic file replacement, strips privileged mode bits, and preserves unrelated/new files. Root-managed `.php` runtime configuration is excluded from file restore application, including full-home restores; explicit selections are rejected. PHP configuration remains managed through panel settings. The worker runs with reduced CPU and I/O priority.

The snapshot and legacy archive systems share a queue lock and check each other's pending/running records. Interrupted restores become failed records with a clear warning that some files may have changed; pre-restore recovery points remain available. Those recovery points currently have no automatic retention cleanup, which must be addressed before shipping this system.

The account APIs add `/snapshots/runs/{run_id}/restore`, `/snapshots/restores`, and `/snapshots/restores/{restore_id}/undo` under the existing account-backup prefix. Despite the internal `undo` endpoint name, the UI accurately calls the operation **Recover previous files**. The API derives its source snapshot from an owned restore record; customers cannot supply an arbitrary internal source override.

File restore verification: **70 backend regression checks passed**, then **18 targeted authorization/recovery checks passed**. The final protected-PHP dataset passed **7 restore checks**, covering real selected/all-file restoration, previous-version recovery, account UID and reduced priority, unchanged/new file preservation, symlink rejection and preservation of root-managed PHP configuration. The production frontend build passed; **10 backup/restore browser checks passed**, followed by **4 final restore checks** after clarifying previous-file recovery behavior in the UI. Browser flows use mocked APIs; backend restore tests use actual encrypted repositories and subprocess privilege changes with disposable homes.

Logs: `/root/boron-setup/snapshot-file-restore-tests.log`, `snapshot-restore-final-tests.log`, `snapshot-protected-restore-tests.log`, `snapshot-restore-priority.log`, `snapshot-restore-all-files.log`, `snapshot-restore-build.log`, `snapshot-restore-browser.log`, and `snapshot-restore-browser-final.log`. These changes have not yet been deployed to the live panel.

## Database streaming and import isolation

`daemon/snapshot_databases.py` now drives raw SQL export for snapshot jobs and provides the database import primitive. Imports stream a verified SQL file through standard input, discard result output, and authenticate using a newly generated, database-specific SQL login. Passwords live only in private temporary option files. The login has the existing hosting privilege set on an exactly escaped database name, with no global privileges or grant option. System schemas are rejected. The client disables local infile and interactive filesystem commands using its supported batch, binary and sandbox options. Temporary users are removed on normal success/failure; the reserved worker-name cleanup helper is intended for startup before any restore workers run.

The temporary-name prefix is longer than the maximum hosting username, preventing collisions with ordinary account-prefixed database users. Cleanup checks the complete reserved name pattern before removing a login.

Testing against the live-equivalent privilege set found that requesting events unconditionally fails even for ordinary WordPress tables. The exporter now checks for views, routines, events and trigger metadata and explicitly refuses a backup containing objects outside the configured hosting privilege set. It never silently omits such objects. This remains a product limitation to account for in the final deployment audit; the panel's currently provisioned database privileges only support ordinary hosting tables. No live SQL privileges were expanded.

The SQL import primitive is tested, but database restore job/API/UI integration, previous-database recovery, recreating deleted databases and recovery metadata remain unfinished. The startup cleanup helper is not yet wired into daemon recovery. These gaps still belong to the active backup goal.

Primary references: [MariaDB client options](https://mariadb.com/docs/server/clients-and-utilities/mariadb-client/mariadb-command-line-client), [MariaDB dump](https://mariadb.com/docs/server/clients-and-utilities/backup-restore-and-import-clients/mariadb-dump), and [database GRANT semantics](https://mariadb.com/docs/server/reference/sql-statements/account-management-sql-statements/grant). Integration tests use an isolated socket-only MariaDB 10.11 instance and verify actual contents, denial of cross-database access (including underscore lookalikes), denial of client/server filesystem operations, cleanup and unsupported-object detection.

Database transport verification: **31 database/process/job checks passed** before the final hardening batch. The final **23 database/process checks passed**, including the actual encrypted backup-job database round trip, existing SQL-file sourcing rejection, local-file import rejection with server-side local infile enabled, reserved-login cleanup, private output paths and INFO-level password-log checks. Logs: `/root/boron-setup/snapshot-database-tests.log`, `snapshot-database-final-tests.log`, `snapshot-database-log-proof.log`, and `snapshot-database-file-guards.log`. All SQL integration tests use a disposable socket-only server; they do not alter the live server's accounts or privileges.

### Database restore jobs (development)

The customer-scoped restore endpoint accepts `kind: databases` and an explicit
`databases` list. The worker checks current account ownership and the hosting
name prefix, verifies every selected SQL file from the encrypted snapshot, and
exports all selected current databases before importing any of them. That export
becomes a separate encrypted safety snapshot recorded in restore history. Previous
version recovery uses that recorded snapshot, never a customer-supplied snapshot
identifier. Progress and completed database names survive worker failures.

Imports retain existing database users and credentials. The daemon removes only
reserved abandoned import logins before recovering queued workers at startup.
Deleted databases currently fail explicitly; account reconstruction and encrypted
credential metadata are still required. SQL imports now replace all ordinary tables in the selected database, including
removal of tables created after the snapshot. Database selection UI, mail/config restores, safety-snapshot retention,
mutation coordination beyond backup queues and live verification remain required.

Validation: 25 real database/file-restore tests passed, including queued database
restore, previous-version recovery, neighboring database isolation and file-restore
regressions. Twelve backup job/API tests also passed, including forwarding selected
database names only through the authorized customer endpoint. Logs are retained at
`/root/boron-setup/snapshot-database-job-tests.log` and
`/root/boron-setup/snapshot-database-job-api-tests.log`.

### Database restore selection (development UI)

Recovery-point details now include a database selector populated from the selected
snapshot's SQL entries. The API enforces account access, and the daemon matches
entries against current account ownership before marking them available. Removed
registrations remain visible but disabled. Listing uses the repository lock and
returns a retryable busy message instead of competing with backup/retention work.

Both themes support selecting databases, typed confirmation, queued progress and
previous-database recovery. History identifies files versus databases and explains
partial failures. The UI explicitly states current import behavior: all current tables
are replaced, later-created tables are removed, and existing users/passwords remain.
Deleted-database reconstruction and other previously
listed backup requirements are still unfinished. No backup code has been deployed.

Validation for database selection: production build passed; 30 backend database/job
checks passed, plus a final real snapshot/SQL round trip proving removed ownership
registrations disable and reject restores. Fourteen browser checks passed across
Evolution and Paper Lantern, light/dark modes, mobile database confirmation,
previous-version recovery and existing archive/file restore flows. Screenshots are
in `/root/boron-setup/snapshot-database-ui-proof`; logs use the
`/root/boron-setup/snapshot-database-` prefix. The snapshot interface is lazy-loaded
and 4.80 KB gzip in this build; no dependencies were added.

### Complete ordinary-table replacement

Queued database restores now clear the selected database's ordinary tables before
importing its verified SQL snapshot. The previously saved encrypted recovery copy
contains later-created tables too, so previous-version recovery can restore them.
Table removal uses the same temporary login with exact database privileges; account
users and passwords remain unchanged. Table identifiers are quoted from database
metadata, including embedded backticks. Foreign-key checks are disabled only within
the reset connection. Cross-database foreign-key relationships cause an explicit
failure before changes; the worker does not damage a neighboring database's links.
Empty SQL files fail before clearing tables. Unsupported views, routines, events,
triggers and special table types are rejected instead of being silently omitted.

Table replacement/import is not transactional. An interrupted or failed import may
leave partial data; its encrypted pre-restore copy remains available. Website writes
must be paused during restoration. Reconstruction of deleted databases, broader SQL
object support, mail/config restores, safety retention and deployment remain open.

Implementation reference: [MariaDB DROP TABLE](https://mariadb.com/docs/server/reference/sql-statements/data-definition/drop/drop-table)
and [foreign-key constraints](https://mariadb.com/docs/server/architecture/server-constraints/foreign-key-constraints).

Validation: final database suite passed 23 tests, including real replacement and
previous-version recovery, foreign keys, quoted table names, neighboring databases,
empty-input rejection and unsupported sequences/system-versioned tables. Production
build passed. All four theme/mode database browser checks passed with the new table
replacement confirmation. Logs: `/root/boron-setup/snapshot-replacement-final-tests.log`,
`/root/boron-setup/snapshot-database-replacement-build.log`, and
`/root/boron-setup/snapshot-replacement-browser.log`.

## Pre-restore recovery-copy retention

Successful backup jobs now apply the configured retention count separately to
successful pre-restore recovery copies for that account, destination and policy.
For example, a count of seven retains seven scheduled recovery points and seven
successful pre-restore copies. Copies from failed/interrupted restores and sources
referenced by pending/running recovery jobs remain protected. Other policies and
unregistered repository snapshots are excluded from this cleanup.

Retention runs under the existing account/repository locks. Repository deletion
must succeed before history loses its recovery action. If a process stops after
repository deletion but before updating history, a retry reconciles the missing
snapshot and completes the metadata update. History explains when a previous
version expired; it does not offer a broken recovery button. The settings form
explains the separate allowance and failed-restore protection.

Verification: 21 backend checks passed, including real encrypted repositories,
queued recovery completing after retention, failed-copy preservation and an actual
repository deletion followed by simulated interruption and retry. Eight browser
checks passed across both themes/light-dark modes for file and SQL restore flows;
production build passed. Logs: `/root/boron-setup/snapshot-safety-retention-tests.log`,
`snapshot-safety-retention-browser.log` and `snapshot-safety-retention-build.log`.
Live deployment is recorded separately after verification. Deleted database
reconstruction, mail/configuration restores and the remaining original checklist
still require work; this change does not complete the backup product.

Deployment completed after queue-idle preflight. All three panel/web services are
active and health/UI/login/administrator identity checks passed after API startup.
The initial immediate probe raced startup; no second restart was needed. Recovery
code archive: `/root/boron-setup/safety-retention-before/code.tar.gz`. This verifies
deployment, while full live backup lifecycle validation remains open.

## Deleted-database recovery metadata (development)

New database snapshots include a private `database-recovery.json` containing the
account name, database/login names, character set/collation and the existing local
login’s native authentication hash. It is created with mode 0600 inside the
private staging directory and stored in the encrypted repository alongside SQL.
The metadata is not returned by an API or stored in the API-readable control DB.
It contains no plaintext database password. Missing registrations/logins or
unsupported authentication/custom TLS requirements fail explicitly rather than
producing misleading reconstruction metadata.

`daemon/snapshot_db_metadata.py` validates account prefixes, local authentication,
hash syntax and charset/collation identifiers. Its reconstruction primitive only
creates an entirely missing database/login pair, preserves the original password,
and grants the existing hosting privilege set with exact database-name escaping.
It refuses existing databases or login names (including alternate hosts), and
compensates resources created by its own call on failure. It never executes saved
SQL account-management statements or grants global privileges. This follows the
MariaDB documented [native-password hash restoration syntax](https://mariadb.com/docs/server/reference/plugins/authentication-plugins/authentication-plugin-mysql_native_password).

This is not yet the customer deleted-database restore feature. The coordinator
must verify the owned snapshot and metadata, handle partially missing resources,
re-check current registrations and collisions, persist reconstruction progress,
and integrate selection/confirmation and recovery after interruptions. Existing
restore behavior is deliberately unchanged until those checks are implemented.
Earlier snapshots lack this metadata and require an explicit compatibility path.

Validation: 46 database/metadata/job tests passed; after the final snapshot-content
assertions, 12 focused tests passed. They use disposable MariaDB and encrypted
restic repositories and verify original-password authentication, restored WordPress
data, preserved collation, denied neighboring databases/underscore lookalikes,
conflicts, compensation, invalid metadata and private file permissions. The
existing job fixture now includes its registered SQL login, matching production.
A read-only live check captured and validated all nine QA databases without
printing credentials or changing SQL resources. Logs:
`/root/boron-setup/snapshot-db-reconstruction-tests.log` and
`/root/boron-setup/snapshot-db-metadata-final.log`. These code changes remain in
development pending restore coordinator integration and deployment.

## Queued deleted-database reconstruction (development)

The snapshot database catalog now distinguishes an existing owned database, a
fully deleted database/login pair with verified recovery metadata, and unavailable
names. The UI permits selecting a reconstructable deleted database and explains
that its backed-up credentials will be restored. Legacy snapshots still restore
existing owned databases; deleted names without reconstruction metadata are
explicitly unavailable. Metadata is restored into private temporary staging,
validated for format/account ownership, and removed after reading. Hashes remain
internal, never serialized to the catalog or restore history.

Trigger authorization checks the owned snapshot catalog. The worker verifies
source SQL and rechecks current registration and SQL resources before changes.
It registers an absent name to the account, reconstructs the database/login with
its saved password and collation, and imports the verified SQL. Progress records
reconstructed names and completed imports. Existing databases retain their
credentials and receive encrypted pre-restore copies. Previous-version recovery
selects only databases that existed before the operation; newly recreated
databases are retained, as explained in the confirmation UI.

A reentrant, cross-process database mutation lock now covers database CRUD/account
database termination, metadata capture/reconstruction, queued snapshot database
restores and the WordPress hard-removal database completion callback. Concurrent
operations receive a retryable busy message. This prevents covered panel operations
from changing ownership between restore checks and SQL creation; a final audit of
other mutation/legacy entry points remains required.

Current limits before deployment: partially missing database/login resources are
explicitly refused. Reconstruction progress and owned registration survive worker
failure, but abrupt interruption between individual SQL creation/grant steps needs
additional reconciliation. Existing resources are not silently adopted. Mixed
existing/deleted recovery and all remaining full backup lifecycle requirements
still need their final audit. This development change is not yet deployed.

Validation: 97 broader database/metadata/backup/WordPress checks passed. A separate
final run passed three checks covering actual queued reconstruction plus real
cross-process exclusion/reentrancy. Six metadata reader rejection checks passed.
Four new browser cases passed across both themes/light-dark modes, including
existing and deleted selections, typed confirmation, mobile layout and previous
version recovery. Production build passed. Logs under `/root/boron-setup`:
`snapshot-queued-reconstruction-tests.log`, `snapshot-db-queue-final.log`,
`snapshot-metadata-reader-tests.log`, `snapshot-reconstruction-selection-browser.log`
and `snapshot-reconstruction-build.log`. The SQL tests use disposable socket-only
MariaDB servers and encrypted repositories, not the live customer databases.

The final name-reuse test passed (`snapshot-db-name-race-tests.log`): after a
reconstruction is queued, a newly created database at that name causes the worker
to fail without replacing its sentinel table/content. Removing only that disposable
test database then allows a newly queued reconstruction to restore the original
WordPress data and authenticate with its original password.

## Partial-resource and interrupted reconstruction recovery

An owned registration now permits repair when only the database or its login
survives. A surviving login must match the saved local native-password identity;
its password is never reset. Unregistered survivors, changed login mappings,
foreign/shared registrations and nonmatching credentials remain explicit conflicts.
Existing database contents receive a pre-restore safety snapshot even when their
login is missing. Reapplying the exact database grant repairs an incomplete
create/database-user/grant sequence without adding global privileges.

The worker persists `reconstruction_pending` before SQL creation. If interrupted,
startup marks the running operation failed while retaining that marker. A newly
requested restore recognizes the owned incomplete reconstruction, verifies its
surviving login against snapshot metadata, repairs the grant and imports the data.
Successful repair clears the account/name's prior pending markers. This is explicit
user-triggered retry, not automatic replay of an interrupted destructive import.
Ordinary exceptions compensate only newly created resources; pre-existing data and
logins are retained.

The mutation audit extended coordination to staging clone/sync/delete, existing
cPanel import database/password steps and legacy full-account/SQL restore workers.
Temporary phpMyAdmin/import identities use reserved prefixes and do not claim
hosting registrations. This coordination covers panel management operations;
application SQL writes still require the maintenance/pause described in restore UI.

Validation: 24 metadata/partial/restart-recovery checks, 39 mixed restore/SQL/CRUD/
coordination checks, 65 staging/import regressions and 54 legacy restore/coordination
checks passed. The interrupted test raises a worker-termination exception between
user creation and grant, runs startup recovery, queues a fresh restore and verifies
original-password authentication and restored WordPress data. Mixed restore/undo
proves that the prior existing database returns to its previous contents while the
newly reconstructed database remains available. Logs under `/root/boron-setup`:
`snapshot-partial-recovery-tests.log`, `snapshot-mixed-recovery-tests.log`,
`database-coordination-audit-tests.log`, `legacy-restore-coordination-tests.log`.
Frontend code is unchanged from the preceding successful production build and four
deleted-database browser cases. Live deployment/verification is recorded below.

Deployment of reconstruction/partial repair completed after all WordPress and
backup/restore queues reported idle. Code recovery archive:
`/root/boron-setup/db-reconstruction-before/code.tar.gz`. The first readiness probe
mistakenly used `/health` and received 404; the correct `/healthz` verifier passed,
along with UI 200, login 303 and administrator identity 200. boron-api,
boron-provisiond and lshttpd were active; no second restart was required. Evidence:
`/root/boron-setup/db-reconstruction-deploy.log`. This is deployment/access proof;
a full live backup/reconstruction lifecycle still needs a dedicated disposable
SQL dataset. No live database was deleted or recreated in this validation.

Concurrency follow-up: the current global SQL mutation guard returns a retryable
busy error. Interactive management should retain that behavior, but background
backup/restore workers should wait or requeue under contention rather than fail a
scheduled job. Address this before calling multi-job backup behavior complete.

## Background contention and live database lifecycle — 2026-09-13

Background snapshot SQL capture/restores, legacy restores and existing cPanel
import workers now wait for the shared SQL mutation lock. Interactive database
management retains a prompt retryable busy error. The lock remains reentrant for
nested operations and coordinates both threads and processes. Credential capture
and SQL export run in one critical section; the worker refreshes registrations
after acquiring the lock and writes that same set into the manifest. Accounts
without registered databases produce empty metadata without opening SQL.

Validation: initial 39 database/job/coordination checks and 89 legacy/import checks
passed. After refreshing registrations under the lock, 17 focused checks passed;
the final empty-registration optimization passed its no-SQL-connection test.
Evidence: `database-worker-wait-tests.log`, `background-restore-wait-tests.log`,
`database-worker-wait-final-tests.log`, `empty-database-manifest-tests.log` under
`/root/boron-setup`. Deployed after a fresh idle preflight; health, UI and admin
login/identity passed. Recovery archive: `worker-wait-before/code.tar.gz` in the
same directory; deployment log `worker-wait-deploy.log`.

Live proof used a newly created disposable database `wpdevqa_recoveryproof`, whose
name was verified absent before creation. Through authenticated panel APIs, the
administrator created a private local repository and a disabled manual policy
scoped only to wpdevqa, with notifications disabled. Backup run 1 completed. The
customer API then deleted only this new database/login pair and queued restore 1.
The original generated password authenticated successfully after reconstruction,
and the original sentinel table/content returned. All nine neighboring database
registrations remained present. Existing WordPress databases were backed up but
were not selected for restore or deletion.

A second unchanged manual backup (run 2) completed and reported 6,819 bytes of new
data, versus 4,123,104 on the first, while processing 4,116,284 bytes. This proves
live job-level data reuse in addition to the isolated storage tests. Destination
and policy IDs are both 1. The disposable database, encrypted recovery points and
private resumable state are retained for further validation; the policy is disabled
and manual, so it does not add recurring scheduled work. Credentials remain only
in the private state/secret files and were not printed.

Both Evolution and Paper Lantern passed live customer-browser checks for the
recovery point, enabled database selector and completed restore history. The first
browser probe used a five-second assertion while the encrypted catalog was still
loading; it was corrected to allow the real request to finish. A second assertion
matched hidden responsive text as well as the desktop table; the final probe
selected the visible table and passed. These were verifier issues. Cold catalog
loading time should still be considered in the final performance polish.

Evidence: `/root/boron-setup/live-database-recovery.log`,
`live-database-incremental.log`, `live-database-recovery-browser-verified.log`,
`live-recovery-evolution.png`, `live-recovery-paper-lantern.png`; resumable helpers
`live-database-recovery.py`, `live-database-incremental.py` and private state under
`live-database-recovery/`. Mail/configuration restore, live SSH lifecycle/filter/
notification coverage and the broader requirement audit remain unfinished.

## Private mail recovery metadata (development)

Mail-component snapshots now include `mail-recovery.json` in private staging and
the encrypted repository. It captures the account's registered mail domains and
SQL-backed mailbox hashes, quotas, active state, forwarding rules, catch-all and
autoresponder settings. SQL IDs are replaced by domain/local-part names and dates
are normalized to ISO strings. Capture explicitly uses a repeatable-read SQL
transaction. The JSON file has mode 0600; existing public mailbox APIs are unchanged
and do not expose hashes. Plaintext authentication schemes and malformed mailbox
recovery fields are rejected.

`daemon/snapshot_mail_metadata.py` also provides a missing-mailbox SQL primitive.
It validates the mailbox fields, locks the existing mail-domain row, refuses an
existing mailbox and inserts the original Dovecot hash/quota/status using bound
parameters. It neither overwrites existing credentials nor creates message files,
activates routing rules or updates panel cache registrations. The future restore
coordinator must validate account/domain/snapshot ownership before calling it.

Validation: 22 metadata/job checks passed. Tests create the actual installer mail
schema in an isolated MariaDB server, verify other domains are excluded, preserve
routing/autoresponder settings and confirm hashes/plaintext are absent from public
listing/log output. A recreated fixture mailbox retains its original hash/quota;
Dovecot's password verifier accepts its original synthetic password. Existing
mailboxes retain their current quota/status. Real restic backup/restore preserves
message bytes and mode-0600 recovery metadata. No email was delivered or sent.
The first run had an ambiguous unqualified `active` column in a test query; the
corrected final run passed. Evidence:
`/root/boron-setup/mail-recovery-metadata-final-tests.log`.

A read-only live source check captured the QA account's one mail domain and one
mailbox successfully, without printing credentials or changing the live mail
service. These development changes have not been deployed. Remaining work includes
Maildir restore/application and service coordination, panel registration updates,
owned snapshot selection/confirmation/UI, previous-message recovery, restoration of
routing/Sieve/DKIM and other mail configuration, deleted-domain handling and live
workflow verification. This metadata primitive does not complete mail recovery.

## Dovecot message restore experiment (development)

`tests/test_snapshot_mail_dovecot.py` exercises the installed Dovecot 2.3.21
using synthetic Maildirs, an isolated configuration and an unprivileged process.
No live mail service, authentication database, SMTP or LMTP delivery is involved.
The experiment exposed a material limitation: reverse `doveadm backup` into an
existing INBOX fails with `INBOX can't be deleted` when restoring an expunged UID.
This is consistent with the Dovecot author's explanation at
https://dovecot.org/list/dovecot/2015-September/102090.html .

The passing test builds a fresh replacement Maildir from the saved source,
retains the displaced directory and switches the offline fixture to the prepared
replacement. It verifies original message contents/IDs, read and flagged state,
Archive subscription, UIDVALIDITY and UIDNEXT, removal of newer messages from the
restored point, and recovery of those newer messages by preparing and applying
the pre-restore safety copy. Validation: one real integration test passed.

This is a restore-semantics test, not a deployed mail restore feature. Production
still needs per-mailbox delivery/client quiescence, authorization, no-follow
staging validation, encrypted safety snapshots, durable recovery of interrupted
switches, and API/UI integration. Do not use the fixture's directory rename
sequence directly against active mailboxes. General Dovecot migration guidance:
https://doc.dovecot.org/2.3/admin_manual/migrating_mailboxes/ .

`daemon/snapshot_mail_files.py` now prepares a fresh Maildir copy inside a
service-owned mode-0700 staging root. Source and destination parent must resolve
inside that root without symbolic-link components; existing destinations and
destinations inside the source are rejected. The entire source is checked for
symlinks/special files and required Maildir directories before copying begins.
Files are opened with no-follow/nonblocking flags and checked again as regular
files. Copies retain message bytes/names and Dovecot control files with private
0700 directory / 0600 file permissions. Hardlinked messages become independent
copies. Any copy failure removes only the newly created destination.

Validation: 12 preparation tests plus the real offline Dovecot restore/undo test
passed together (13 total). Coverage includes outside/linked/nested destinations,
file and directory links, FIFO rejection, incomplete Maildirs, existing-target
retention, exact binary bytes, private permissions, hardlinks and simulated disk
full. The primitive is not yet connected to the restore coordinator or deployed;
it does not change ownership for Dovecot or touch live mailboxes.

The same module now provides `build_maildir`, which consumes the validated copy
and builds a fresh mailbox through the installed Dovecot binary. A root-owned
temporary directory permits traversal only by the configured mail-service group;
the isolated configuration is root-owned and group-readable. Source, home and run
directories are owned by the unprivileged worker. `setpriv` clears supplementary
groups, drops UID/GID and sets no-new-privileges before executing Dovecot. No live
configuration, authentication database, delivery service or mailbox is opened.
The original private staging ancestors remain mode 0700 and the original saved
files are not changed. Worker UID/GID zero and malformed identities are rejected.

Dovecot first initializes any missing source indexes, then performs a forward
backup into a new replacement Maildir. The temporary root is made private again
before validating and copying the generated output back to service-owned staging.
Failures, including command timeout, clean up temporary work and the newly created
destination while preserving the original saved tree. Dovecot output is not
included in public error messages. Preparation is bounded by command timeouts.

Validation: 22 focused tests passed, including production-worker preparation in
the real restore/undo test, byte-for-byte source preservation, empty and unindexed
Maildirs, process privilege arguments, and open/backup/timeout failure cleanup.
This worker remains development-only. Delivery/client coordination, crash cleanup
of abandoned temporary workspaces, durable application/rollback, ownership-aware
API selection and UI integration still need implementation before deployment.

## Per-mailbox access guard (development)

`daemon/mail_restore_gate.c` implements a Dovecot 2.3 checkpassword userdb guard,
to precede the SQL userdb with `result_failure = continue` and
`result_internalfail = return-fail`. An unblocked lookup exits 3 to delegate to
the real userdb; a blocked lookup exits 111 for a temporary internal failure.
It never authenticates, reads a password, changes SQL active flags or outputs
credentials. The directory must already exist and be root-owned/non-writable by
other users; unreadable/missing/unsafe directory state fails closed. The helper
checks SHA-256 filenames derived from lowercase addresses, supporting addresses
longer than a filesystem component without placing addresses in filenames.
Marker symlinks block access without being followed. Build requires a C compiler
and OpenSSL development headers (`cc ... -lcrypto`); no binary is committed.

Tests compile with warnings treated as errors and exercise the fd-3 interface.
A separate Dovecot process using only temporary Unix sockets proves lookup
delegation, temporary lookup failure, unaffected neighboring mailbox lookup and
resumption after removing the marker. LMTP recipient negotiation returns 451 for
the blocked mailbox and 250 for another mailbox. No DATA command or message was
sent. Initial fixture errors were corrected (assertion wording and duplicate
listener definition); final focused checks passed. Protocol reference:
https://doc.dovecot.org/2.3/configuration_manual/authentication/checkpassword/ .

This guard is not installed or enabled in live Dovecot. Checkpassword is supported
by this Ubuntu 24/Dovecot 2.3 installation but removed in Dovecot 2.4, so future
upgrades need a replacement. Crucially, blocking new lookups does not drain
already authenticated clients or LMTP recipients accepted before the marker.
Those sessions must be coordinated and tested before any live mailbox switch;
durable marker management, startup recovery, installer integration and performance
measurement also remain required. Do not treat the guard alone as a restore lock.

`daemon/snapshot_mail_guard.py` now manages persistent marker ownership. Markers
contain a restore job ID and an unpredictable ownership token in private files;
the mailbox address maps to the same lowercase SHA-256 name used by the C guard.
Creation is exclusive, marker and directory entries are fsynced, and directory
flocks serialize acquisition/release. Incorrect job IDs/tokens, corrupt markers
and stale releases leave protection intact. No context manager automatically
releases a mailbox when a worker fails. The coordinator must establish a usable
mailbox state before calling release. Guard storage is configured by
`mail_restore_guard_dir`, defaulting to the C helper's built-in path; deployment
must keep these paths consistent. Marker tokens belong in private restore state,
not public API responses.

Validation: 22 guard tests passed together, then seven ownership tests passed
again after adding parent-directory fsync. The real isolated Dovecot test now
uses the production Python marker manager. With `lmtp_user_concurrency_limit=10`,
it accepts a synthetic recipient before blocking the mailbox, confirms the anvil
`LOOKUP lmtp/<address>` count remains one, verifies new recipient attempts get
451, then uses RSET to end the original transaction and observes zero. No DATA
command was issued. This proves an already accepted recipient outlives creation
of the guard; it does not prove the full mailbox is quiescent. Dovecot's LMTP
anvil tracking is disabled when `lmtp_user_concurrency_limit=0`.

Source inspected: Dovecot `src/lmtp/lmtp-local.c` in tag 2.3.21.1,
https://github.com/dovecot/core/blob/2.3.21.1/src/lmtp/lmtp-local.c .
Anvil tracking alone is insufficient: a lookup admitted before guard creation
could complete after a zero observation, and authenticated IMAP sessions remain
separate. Admission/draining coordination, durable switch/recovery and installer
integration remain unfinished. These changes are not enabled on the live server.

## Atomic Maildir exchange (development)

`daemon/snapshot_mail_exchange.py` resolves the configured mail root and validated
domain/local-part through no-follow directory descriptors. A prepared sibling
must use a generated `.boron-mail-ready-<32 hex>` name. Planning records the home,
current Maildir and replacement device/inode identities. The coordinator must
persist that plan before applying it. Application rechecks identities, exchanges
both names in one Linux `renameat2(RENAME_EXCHANGE)` operation, and fsyncs the
parent directory. There is no non-atomic fallback and neither tree is deleted.
Inspection recognizes ready/applied states from identities, including a worker
exit after the syscall but before completion recording. Duplicate apply and
stale/foreign-directory plans are refused. Undo exchanges the same retained
directories back, again subject to service coordination.

Validation: 55 combined recovery tests passed. This includes an actual forked
worker exit immediately after the exchange, followed by state inspection and
undo in the parent, plus simulated fsync failure, symlink rejection and stale
plans. The offline Dovecot restore/undo test was then connected to this production
exchange primitive; its 14 focused Dovecot/exchange checks passed. Message IDs,
flags, subscriptions, UIDVALIDITY/UIDNEXT and recovery of newer messages remain
covered. These process-crash tests do not simulate storage hardware power loss.

No live switch is enabled. For the first full-restore implementation, the service
coordinator must briefly stop Dovecot and verify all its processes have exited
before exchanging directories, then resume it. The replacement should be fully
prepared beforehand to keep that pause short. This affects existing mail client
connections server-wide and must be stated in the restore interface; Postfix
must retain incoming mail for retry while Dovecot is unavailable. A service
restart/recovery mechanism, exact safety-point capture, durable journal wiring,
installer support and live proof remain required. The per-mailbox guard still
protects an incomplete restore after mail service resumes. An anvil count alone
must not replace the service barrier until its admission races are resolved.

## Supervised service pause (development)

`daemon/snapshot_mail_service.py` now provides the supervision mechanism for the
short offline switch. It checks that Dovecot is running with
`KillMode=control-group`, then launches a named transient systemd oneshot unit.
`ExecStartPre` stops mail; `ExecStopPost` starts it after success, command failure,
worker termination or timeout. The operation has bounded start/stop timeouts and
runs independently of the calling panel process. A separate `require_stopped`
barrier checks inactive/dead state, zero main/control PIDs and complete process
group shutdown before the exchange worker may modify mailboxes. Initially stopped
services are rejected rather than implicitly started by a restore request.

The operation ID must be persisted before launch. On a caller observation timeout,
the coordinator must inspect that same transient unit and private exchange journal
instead of launching again. A resumed service does not imply a successful mailbox
restore: failed operations report failure and guards remain until journal and
directory state are verified. This internal function accepts only trusted service
commands; it is not exposed as a customer command execution API.

Validation: six real systemd tests passed using unique temporary
`boron-mail-test-*` services whose only normal command is sleep. Tests prove the
switch command starts only after the test service is stopped, restart after both
normal completion and SIGKILL, refusal of initially inactive/arbitrary services,
and independent completion/restart after the calling panel process is killed.
Only test-owned units were created/stopped/removed. Live Dovecot was not stopped.

The supervised mechanism is not yet wired to a persistent mail restore job or
enabled in the panel. The exchange worker entry point, journal validation,
guard installation, exact safety-copy handling, account ownership checks and
UI remain required. Full server reboot recovery and failed service restart also
need coordinator handling; systemd supervision alone does not complete recovery.

## Private journal worker (development)

`daemon/snapshot_mail_journal.py` now creates exclusive, fsynced mode-0600 switch
journals beneath private root-owned snapshot storage. Journals contain the job
and operation IDs, intended direction, mailbox names, guard ownership tokens and
device/inode exchange plans. Reads are bounded and reject symbolic links, unsafe
permissions, duplicate mailboxes, malformed tokens and invalid plans. Tokens are
never included in the worker command line or inspection output.

The worker verifies that the mail service is fully stopped, holds the guard
directory lock while verifying ownership of every selected mailbox, checks all
plans before changing any directory, then performs the atomic exchanges. Guards
are retained after both success and failure. Interrupted batches are inspected
per mailbox; blindly replaying a partially applied batch is refused. A launcher
uses the persisted operation ID and invokes the private module through systemd;
systemd's working directory is pinned to the running code tree.

Supervision now uses one stable switch unit per mail service instead of a separate
unit for each operation. This supersedes the earlier operation-specific unit
naming: the production unit is `boron-mail-switch.service`, with operation ID in
its description and journal. Systemd refuses overlapping transient creation,
preventing independently resumed services around concurrent exchanges even if a
caller dies. Recovery must inspect the unit's operation identity before acting
on it, and explicitly resolve a retained failed unit before scheduling another
operation. Test services use their own distinct stable unit names.

Validation includes real systemd execution of the journal worker against synthetic
maildirs: complete batches switch both mailboxes, interrupted batches retain one
applied and one ready mailbox, the test service resumes and all guards remain
owned. Focused tests also cover wrong guard ownership preventing the entire batch,
service barrier refusal, malformed/public/symlink journals, exclusive creation,
launcher arguments and replay refusal. These checks use test-only mail services;
live Dovecot has not been paused or changed. Final focused result: 27 tests passed.

This is still not an enabled customer restore feature. The coordinator must supply
account authorization, encrypted snapshot selection and safety retention, copied
Maildirs at the correct ownership/location, installed Dovecot guards, persistent
job status/recovery decisions, service restart failure handling and UI. Full
reboot recovery and the live customer workflow remain unverified.

## Mailbox recovery catalog (development)

`snapshot_mail_metadata.read_mailboxes` now reads bounded private metadata files,
validates the owning account, format, domain/mailbox uniqueness, active flags,
quota limits and supported password hashes, and returns mailbox metadata only.
Routing/autoresponder configuration is not passed through this reader. Symlinks,
unsafe permissions and malformed metadata are rejected without including saved
authentication material in error messages.

The account-scoped `/snapshots/runs/{run_id}/mailboxes` API and reporting RPC
decrypt only the owned snapshot's private mail recovery metadata. The public
catalog is explicitly constructed from address/domain/local-part, saved quota and
active state, action/availability and explanation fields. It never includes hashes
or guard tokens. It checks current mail-domain ownership before querying live
mailbox listings; foreign-owned domains are unavailable without querying their
mailboxes. Existing mailboxes and deleted mailboxes eligible for reconstruction
are distinguished. Missing mail-domain provisioning remains unavailable pending
the domain recovery coordinator. Older points without mailbox metadata return an
empty catalog.

This endpoint is a read-only catalog and is not deployed yet. It does not enable
mail restore submissions or show a working restore button; job execution and UI
must be connected before the customer workflow is considered complete.

Validation: 33 catalog/metadata/backup-job tests passed. The real encrypted mail
backup test now reads the public catalog, verifies saved credentials are absent,
recognizes deletion of its isolated SQL mailbox as reconstructable, and rejects
another account before any decryption. API checks reject cross-account mailbox
catalog requests. Additional tests cover malformed metadata, duplicate entries,
unsafe files and foreign-domain ownership. Only isolated SQL fixtures were
changed; no live mailbox was deleted. One existing TestClient dependency
deprecation warning was emitted.

## Prepared mailbox placement (development)

`snapshot_mail_files.stage_for_exchange` now places the built mailbox beside its
live Maildir before pausing mail service. It verifies the source is beneath private
service-owned staging and opens the destination home through validated, no-follow
directory descriptors. An exclusive generated sibling is copied through directory
FDs; links and special files are rejected. Files and directories receive the mail
service UID/GID and private 0600/0700 permissions. Jobs can supply an already
persisted prepared-directory name; existing copies are never overwritten. File contents and directory
entries are fsynced before returning. Failed copies remove only their newly
created sibling after checking its identity; the live Maildir and private source
remain intact. Account ownership authorization remains the coordinator's duty.

The real offline Dovecot restore/undo test now uses this placement function, the
production preparation worker and the atomic exchange. All 39 focused file/
Dovecot/exchange checks passed, including ownership, failed-copy cleanup and
mailbox-home symlink rejection. An initial sandboxed run could not perform chown;
the permitted run on temporary fixtures passed.

Normal mail backup jobs exclude only recovery siblings matching the configured
mail root's `domain/mailbox/.boron-mail-ready-*` location. Explicit safety backups
retain their ability to save these displaced trees. The real encrypted mail-job
test proves the sibling is absent from an ordinary backup and present with exact
bytes in a separate safety snapshot. No live staging or mailbox switch has been
performed. This does not yet connect customer restore submission, recovery-point
retention or job cleanup to the new mail worker.

The additional storage and backup-job regression run passed all 21 tests, with
one existing TestClient dependency deprecation warning. The real encrypted-mail
exclusion/safety test passed separately. Persisting the prepared name in the
coordinator before placement remains required for deterministic crash cleanup.

## Placement receipts and inspection (development)

Mailbox placement can now write a private receipt before creating the sibling.
The receipt records restore ID, mailbox, generated name and home identity, then
durably records the created directory identity in a `copying` phase before
copying message bytes. Successful placement atomically publishes `ready` with
file/byte counts. Receipt creation is exclusive; another job's record is never
overwritten. Updates use temporary files, fsync and atomic replacement. A failed
or interrupted write may leave an older phase, which requires inspection rather
than assuming completion.

`inspect_placement` validates private bounded receipt data and compares inode
identities through the mailbox home descriptor. It distinguishes copying/ready
siblings, a prepared tree that has been exchanged into `Maildir`, a missing
sibling and a planned-but-unconfirmed creation. Changed home identities or
substituted directories are rejected. It never deletes/adopts files or changes
job status: recovery must first confirm the worker is terminal and revalidate
account ownership. A ready receipt alone is not sufficient for cleanup.

Validation: all 44 focused file/Dovecot/exchange tests passed. A forked worker
exits during placement after its identity receipt is persisted; the parent finds
the exact incomplete copy and confirms live mail is retained. Tests also cover
exclusive receipts, observed pre-copy persistence, completed counts, real atomic
exchange/undo recognition and rejection of a substituted sibling. These changes
are development-only and still require the restore coordinator to request and
consume the receipts.

## Guard activation preflight (development)

`snapshot_mail_guard_config` renders the guard userdb block and verifies the
effective configuration using the installed `doveconf` parser. Preflight requires
a root-controlled regular executable, private root-owned guard storage, no
symlink paths, matching compiled/runtime guard directory, disabled authentication
caching, and the guard as the first userdb with the required failure/skip policy.
An unexpected successful guard result also fails closed. The compiled C helper
now supports `--guard-directory` for this non-secret compatibility check.

Both the private journal launcher (before pausing service) and its production
worker verify guard readiness. The production executable location is
`/usr/local/libexec/boron-mail-restore-gate`. Merely creating guard files is no
longer sufficient to launch a production mailbox switch. Test-only service
workers continue to use isolated service fixtures; journal unit tests mock the
separately tested configuration preflight.

Validation: 37 focused guard/configuration/journal tests passed. Actual compiled
fixtures and `doveconf` prove the accepted configuration and rejection of later
guard ordering, enabled caching, permissive failure/success/skip policies,
compiled-directory mismatch and a writable guard executable. Installation of the
binary and ordered configuration, rollout to existing servers and the customer
restore coordinator remain unfinished. Live Dovecot configuration is unchanged.

`snapshot_mail_guard_config.install_binary` now builds the guard with compiler
warnings treated as errors, stack protection, fortified calls and RELRO/NOW
linker hardening. It compiles for the configured marker directory, verifies the
result's reported directory, sets root ownership/mode 0755, fsyncs it and replaces
the installed executable atomically. Compilation happens in an exclusive file on
the target filesystem. Existing unsafe/symlink executables are refused; a guard
compiled for a different storage directory requires an explicit migration rather
than silently abandoning existing markers. Storage and executable directories
must have appropriate root-controlled permissions. The function does not write
Dovecot configuration or reload the service.

The installer package list now includes `libssl-dev` alongside its existing
`build-essential`, supplying the guard's OpenSSL headers. Wiring binary creation
and ordered configuration activation into fresh-install/update flows remains
pending; this function has only been executed against test paths so far.

Validation: 27 compiled-guard/configuration tests passed and `bash -n` accepted
the installer script. Tests verify a working atomic replacement, preservation of
the old executable on compiler failure, removal of temporary compiler output,
storage-path mismatch refusal and symlink refusal, alongside the existing guard
protocol and isolated LMTP checks.

## Production privilege correction and binary installation

Testing the guard with Dovecot's normal unprivileged authentication process
exposed a real incompatibility hidden by earlier root-auth fixtures: that process
could not traverse the mode-0700 marker directory. The C helper now opens the
directory using `O_PATH` and tests marker existence using `fstatat`. Guard storage
is root-owned, group `dovecot`, mode 0710. Dovecot can traverse a known name but
cannot list directory contents; ownership-token files remain root-only mode 0600.
Python marker management and installation/preflight enforce these permissions.

The live panel data parent `/var/lib/boron` is mode 0750, so putting the guard
under it would still be inaccessible. The default is now the separate
`/var/lib/boron-mail-restore-gates`, superseding the earlier nested default. This
does not relax panel data permissions. The helper's new `--check-access` probe is
run as the `dovecot` UID/GID during build verification and readiness checks,
detecting inaccessible ancestors before publication/activation.

Validation: 50 affected guard/journal/configuration tests passed together; all 13
configuration tests then passed after adding explicit ancestor-denial coverage.
Real isolated LMTP tests now run with both root and unprivileged auth processes.
A direct process running as dovecot proves marker stat works while listing the
directory and reading a root-owned token file both fail with PermissionError.
Fixtures use standalone temporary paths so pytest-private ancestors do not mask
the service's actual access requirements.

The tested binary is now installed on the development server at
`/usr/local/libexec/boron-mail-restore-gate`, with the new restricted marker
directory. Compilation and the Dovecot-identity access probe succeeded on the
real paths. Dovecot configuration has not been changed or reloaded; activation,
installer/update wiring and customer restore coordination remain outstanding.

## Guard configuration activation

`install_configuration` now preserves the original Dovecot configuration in a
private root-owned backup, atomically writes an ordered managed include, and
validates the effective guard configuration before an optional reload. Validation,
reload or post-reload health-check failures restore the original configuration;
reload attempts also reload the restored configuration on failure. Existing
unmanaged fragments are refused. Repeated installation retains one managed include.

Validation: all 17 configuration tests passed, including exact original-file
restoration on validation and post-reload health-check failures. The development
server now has the guard activated. Independent live verification confirmed the
QA mailbox still resolves, a temporary marker for a nonexistent probe address
returns a retryable lookup failure, and removing that marker restores the normal
unknown-address result. The marker directory is empty and Dovecot remains active.
No mail messages were sent or mailbox contents changed by these checks. The
original live configuration is retained in a private backup under
`/root/boron-setup/mail-guard-config-backups`.

This activates the lookup guard only. Customer mailbox restore submission,
coordinator recovery and installer/update wiring still require implementation.

Fresh installation now invokes `daemon.snapshot_mail_guard_config` after mail
configuration and before Dovecot service activation. The entry point builds the
binary, saves the original configuration and validates the managed include.
Failure aborts activation; it does not print configuration-bearing exception
data. The installer starts/restarts Dovecot afterwards. Existing-server update
wiring remains pending. Installer and guard validation: 23 passed, one skipped
because ShellCheck is unavailable; Bash syntax and installer dry-run passed.

`snapshot_mail_service.inspect_switch` observes the fixed supervisor unit against
the persisted operation identifier. It distinguishes a running operation from a
terminal or garbage-collected unit without launching or retrying work. A queued
systemd job or remaining main/control process prevents a terminal classification,
including the service restart in ExecStopPost. Another operation's unit and
observation failures require retaining recovery state. A missing unit is not
evidence of a successful exchange: the coordinator must still inspect its inode
journal. Twelve supervision tests passed, including real systemd worker failure,
continued observation after caller SIGKILL, missing units and operation mismatch.
The observation API still needs integration into the customer restore coordinator.

Journal recovery now consumes that observation API. It waits without inspecting
changing directories while the supervised worker is running. After termination or
unit collection, it verifies retained guard ownership and classifies directory
identities as fully applied, not applied or partially applied. Failed mail service
resumption is reported separately; a worker exit code alone does not establish
the restore outcome. Recovery reports omit private guard tokens.

For a fully or partly applied forward switch, `prepare_rollback` writes an
exclusive new private undo journal covering only the changed mailboxes. It keeps
the original journal, operation identifier and both directory trees intact and
rechecks identities before persistence. The caller must retain its account lock
and separately supervise the undo worker. Running workers, unavailable mail
service and attempts to automatically reverse an undo journal are refused.

Validation: 28 journal/supervision tests passed. Real isolated systemd workers
exercise both complete and interrupted two-mailbox switches, then execute the
generated undo journal and prove both original directory identities are restored.
The interrupted case rolls back one changed mailbox while retaining the other.
Guards remain owned throughout. These are coordinator building blocks; customer
submission, safety snapshot finalization, guard release and startup recovery are
still not enabled for mailbox restoration.

`snapshot_mail_restore.prepare` now connects account authorization, encrypted
recovery metadata, selected-path decryption and offline Dovecot rebuilding. It
checks snapshot ownership, current account status and every selected domain's
ownership before looking up any mailbox SQL users. Duplicate addresses collapse
to one selection. Missing recovery metadata or missing backed-up Maildir trees
are refused; an excluded tree must not be mistaken for an empty mailbox.

Preparation uses an exclusive private directory, cleans it on failure and returns
private entries for the coordinator. Those entries include hashes for later
deleted-mailbox reconstruction and must never reach public summaries or API
responses. Existing SQL users and live mail trees are unchanged. The coordinator
must retain account/repository locks, recheck ownership before exchange and persist
placement receipts before moving prepared files beside live mailboxes.

Validation: 22 metadata/catalog tests passed. The real encrypted-restic and
isolated-MariaDB fixture now prepares its selected mailbox through this path and
proves reconstructed message bytes and read flags survive while the source stays
unchanged. Mixed owned/foreign selections are rejected before mailbox SQL lookup;
suspended accounts are refused. Customer restore submission and finalization are
still pending.

Mailbox preparation now has a batch staging handoff. `snapshot_mail_restore.stage`
rechecks account/domain ownership and private source paths, then persists an
exclusive mode-0600 inventory before copying any mailbox beside its live Maildir.
The inventory binds account and restore IDs to generated sibling names and
individual placement receipts. It contains no password hashes. Interrupted
staging deliberately retains the work directory, inventory and completed copies
for recovery; re-running the batch against that directory is refused.

Validation: 31 staging/file tests passed. An injected interruption before the
second mailbox proves the first copy has a ready receipt, the second still has
an inventory entry without a receipt, both original live Maildirs remain unchanged
and a repeated batch cannot overwrite the recovery inventory. Missing-mailbox
home provisioning, inventory recovery decisions and the final customer-facing
coordinator remain outstanding.

`inspect_staging` now reads bounded private placement inventories and binds them
to the requested account/restore IDs. It checks current domain ownership before
inspecting mail paths and binds each receipt to its inventory's mailbox, restore
ID and generated sibling name. It reports ready/copying/exchanged/missing receipt
states, not-started entries with neither receipt nor sibling, and unconfirmed
siblings without receipts. It does not adopt or remove unconfirmed paths.

The caller must hold the account lock and establish placement-worker termination
before using these observations. Suspended accounts can still be inspected for
recovery, but missing accounts or transferred domains are refused. Validation:
31 staging/file tests passed, including mismatched restore IDs, changed receipt
IDs, unconfirmed sibling paths and domain reassignment. The full customer restore
coordinator and automatic recovery actions remain unfinished.

Deleted-mailbox recovery now has guarded Maildir initialization. The restore must
own the mailbox's durable lookup guard before `initialize_maildir` creates missing
domain/home/Maildir/cur/new/tmp directories. Traversal uses directory descriptors
and refuses symbolic links. Newly created directories receive vmail ownership,
mode 0700 and file/directory fsyncs; existing directories must have the expected
ownership and safe permissions and are otherwise retained unchanged. Existing
messages are never removed. A failed initialization retains its partial tree and
guard for inspection rather than attempting destructive cleanup.

Validation: 40 Maildir/guard tests passed, including wrong restore ownership,
idempotent initialization with a retained message, symlink refusal and unexpected
directory ownership. This is still an internal recovery primitive: account/domain
authorization and SQL recreation must be coordinated before customer submission
is enabled.

Restore guard acquisition now persists a private account/job-bound token inventory
before activating any mailbox block. Exclusive inventory creation prevents a
retry from replacing the ownership tokens of an interrupted batch. Failures leave
that inventory available for subsequent recovery; acquired guards are not blindly
released.

Guard publication now writes and fsyncs complete ownership data in a temporary
file, then hard-links it atomically to the final marker name without overwriting
an existing guard. A failed write cannot publish partial JSON. A failure after
publication leaves a complete marker matching the pre-persisted token. This
supersedes the earlier direct-write behavior for newly acquired guards; existing
corrupt markers still require explicit recovery.

Validation: 43 guard/staging/journal/Dovecot tests passed, including failure before
publication, failure after publication with successful owned recovery, duplicate
acquisition refusal and private batch-token persistence. Full coordinator
finalization and startup recovery remain pending.

`provision_mailboxes` now connects owned guard acquisition to Maildir creation,
missing SQL-user reconstruction and the panel's MailUser cache. It rechecks the
account selection, verifies the exact guard set and writes an exclusive private
provisioning intent before changing storage or SQL. Existing SQL mailboxes retain
their current password, quota and active status; missing ones use saved recovery
metadata. Cache quota is read back from SQL, and current domain registration is
checked before cache writes. Database operations use the background mutation lock.

The provisioning record becomes completed only after all selected mailboxes and
cache rows are ready. An interrupted or repeated attempt is not blindly replayed;
its intent and guards remain for recovery. Two real isolated-MariaDB integration
tests passed, proving both missing-user reconstruction and preservation of an
existing disabled mailbox with a changed quota, plus cache synchronization,
Maildir creation and retained guard ownership. Customer submission, interrupted
provisioning reconciliation and restore finalization remain outstanding.

`create_switch` now connects account-verified placement inventory and the exact
owned guard set to a private executable switch journal. Every placement must be
ready before journal creation; the journal records current/replacement directory
identities and a fresh operation ID. Creation is exclusive, so a repeated call
cannot replace an operation's recovery record. This step does not exchange mail;
the supervised worker still stops Dovecot and revalidates directory identities.

Validation: 17 staging/journal tests passed, including refusal of a partially
staged batch, completion of its remaining isolated copy, journal creation with
both directory states still ready, duplicate-journal refusal and the existing
real supervised exchange/rollback tests. Customer submission and post-switch
safety backup/finalization remain outstanding.

Post-switch displaced-mail backup is now connected through `backup_displaced`.
It verifies account/domain ownership, forward-job identity, terminal applied
directory state and resumed mail service before archiving. It retains guard
ownership while encrypting the displaced trees and a private mailbox/path mapping
in the account's restic repository. A private result receipt records the returned,
ownership-verified snapshot ID. Both live/displaced trees and guards remain intact;
this function neither finalizes the restore nor removes staging.

The exclusive safety manifest prevents blind repetition after an interrupted
backup; reconciliation of a repository commit without its result receipt remains
to be implemented. A real isolated-restic integration test passed after correcting
fixture setup order. It proves the recovery point contains the original messages,
the live temporary Maildirs retain restored messages, the mapping excludes guard
tokens, premature backup is refused and guards remain owned after success.

Safety snapshots now carry a validated `mail-safety:<operation-id>` tag alongside
the account ownership tag. `backup_displaced(..., recover=True)` verifies the
private intent against the original switch, then requires exactly one owned
snapshot with that operation tag and the expected archived path set. It can
recreate a missing local result receipt without repeating the backup. An existing
receipt must agree with the repository. Missing or ambiguous archives are refused;
all guards and trees remain retained. The caller must establish backup-worker
termination and hold the account/repository locks before reconciliation.

Validation: all 10 safety/storage tests passed, including simulated loss of the
local safety receipt, idempotent reconciliation, duplicate-operation refusal,
local and SSH incremental restore, host-key enforcement, encryption-key rejection
and account-isolated retention. The mailbox job's finalization and startup
recovery wiring remain unfinished.

Forward-restore finalization is now available internally. It requires a terminal
switch worker, resumed mail service, valid production guard configuration,
current account/domain ownership, existing SQL mailboxes, applied directory
identities and private vmail-owned live Maildirs. It verifies the safety receipt
against the owned repository snapshot, operation tag and archived path set.

Before releasing guards, finalization persists a private release intent binding
account, restore, operation and safety snapshot IDs. Batch release verifies every
remaining marker before removing any; already-missing markers are allowed only
through this recovery path. Another job's marker prevents release. Re-entry after
an interrupted release revalidates the saved intent and mailbox/snapshot state.
It retains displaced trees and private records and returns completion evidence
for the job coordinator to persist.

Validation: 11 safety/guard tests passed. The encrypted-snapshot integration now
injects interruption after one guard is released, resumes finalization, verifies
all remaining owned guards are released and checks idempotent re-entry. A separate
test proves a later mismatched owner prevents removal of an earlier valid guard.
Customer submission, job-state integration and full startup recovery remain
unfinished.

`run_restore` now assembles a new mailbox restore through encrypted preparation,
owned guard acquisition, missing-user/storage provisioning, staged copying,
exclusive journal creation, supervised switching, displaced-mail safety backup and
verified finalization. It requires a checkpoint callback, supplied by the job
coordinator, before guards/live changes and at each later phase. Checkpoints carry
work/journal references and safety snapshot IDs, never credentials or tokens.
The entry point deliberately does not replay failed work or delete its recovery
records. Job-state persistence and startup reconciliation still need wiring before
customer restore submission is enabled.

The assembled-workflow integration test passed after correcting fixture Maildir
subfolder ownership. It uses real isolated SQL, encrypted restic storage and
offline Dovecot rebuilding; only systemd supervision is substituted for its
temporary mail tree (the separate journal/service suites exercise real systemd).
It proves backed-up messages replace current messages, newer mail remains
recoverable from the safety snapshot, checkpoint payloads contain no credentials,
and all owned guards are released at completion.

Mailbox workflow checkpoints now have a dedicated `SnapshotMailRecovery` table,
created additively by normal schema initialization. `_mail_checkpoint` persists
strictly ordered phases, fixed private work/journal references and the safety
snapshot ID alongside the existing restore job. It rejects out-of-order phase
changes, changed recovery paths/snapshots and completion without explicit guard
release evidence. Successful completion updates the job's public mailbox-count
summary; internal path references are not part of its serializer.

The assembled restore integration passed with real SQLite checkpoints replacing
the earlier in-memory callback. It verifies rejection of premature completion,
completed job/safety state through a fresh session, persisted recovery references
and their absence from the public restore response. Dispatcher submission and
startup recovery still need wiring before customer mailbox restore is enabled.

The snapshot background dispatcher now routes internal `kind=mail` jobs through
the assembled workflow and durable checkpoint callback. It validates the source
includes mail before entering that workflow. Failures after a persisted work
reference retain the job as running/recovery-pending, preserving account-level
conflict protection and private artifacts. Failures before that point become
failed. Mail failure responses/logging use generic diagnostics rather than
printing potentially credential-bearing exceptions.

The generic startup restore sweep now leaves interrupted mailbox jobs active for
mail-specific recovery instead of marking them terminal and allowing conflicting
work. Automatic phase-specific reconciliation and customer trigger support remain
unfinished; this dispatcher route is not yet exposed by the restore API.

Validation: three dispatcher/integration cases passed: failure before preparation,
failure after persisted preparation with recovery artifacts retained across the
startup sweep, and a completed encrypted mailbox restore through `execute` with
real job checkpoints and safety snapshot state. The integration still substitutes
supervision for its temporary tree; separate systemd suites cover that boundary.

Startup now dispatches mailbox-specific reconciliation. `recover_mail_restore`
locks the account/repository, binds its private journal to the restore ID and
observes the original switch unit. A running unit is reobserved after five seconds
without launching another switch. Once terminal, recovery preserves or reconciles
the displaced-mail safety snapshot and runs verified finalization. An existing
release intent supports partial/already-completed guard release. Completion and
checkpoint state are committed together, clearing the interruption error.

Jobs interrupted before any persisted work reference are failed before activation.
Earlier incomplete preparation, partial switches, missing/ambiguous safety state
and other unverified conditions retain active recovery protection. Those paths
still need further reconciliation; customer mail restore submission is disabled.

Validation: three dispatcher/integration cases passed. The assembled restore test
now simulates loss of the final database completion update after guard release,
then proves reconciliation returns both job and checkpoint to completed, clears
the interruption error and never invokes another mailbox switch. Earlier failed
preparation remains protected when processed by the startup dispatcher.

Large mailbox selections now fit the storage layer: operation-tagged mail safety
backups allow 1,001 paths (1,000 mailboxes plus manifest), and selected restore
allows 1,000 paths. Ordinary backups retain their existing 200-path bound.
`entries_many` validates ownership once and lists multiple directories in one
restic call. Selected-path restore and mailbox preparation use this batching,
avoiding one archive listing per selected mailbox while retaining exact path
existence and snapshot-containment checks.

Validation: all 10 storage tests passed, including a real 1,000-directory safety
backup and selected restore verifying all message contents and exactly one path
listing, plus existing local/SSH regression cases. The assembled mailbox workflow
also passed with batched preparation, persistent dispatcher checkpoints and
interrupted-finalization recovery. This verifies storage scale; it is not yet a
1,000-mailbox live Dovecot switch test. Customer submission and remaining recovery
cases are still unfinished.

Startup can now terminate proven pre-switch interruptions. `abort_pre_switch`
requires the absence of a switch journal, validates private guard inventory and
current domain ownership, and refuses a provisioning record still marked planned.
With no guard inventory, inconsistent later preparation records are refused.
Otherwise a durable abort intent precedes owned batch guard release, allowing
re-entry after partial release. Staged copies and private evidence are retained.

The job becomes failed/interrupted before switching rather than remaining active
forever, restoring availability for subsequent operations. This does not apply to
partial SQL provisioning or any persisted switch; those still need their own
reconciliation. Fifteen dispatcher/guard tests passed, covering absent/completed
provisioning, retained planned provisioning, idempotent owned release, preserved
staging and startup handling before/after preparation.

Pre-switch abort now reconciles planned SQL provisioning records first. Under the
database mutation lock, it binds the provisioning and guard inventories to the
account/job, verifies current domain ownership and all guard tokens, then reads
actual SQL mailbox state. Existing SQL users retain their credentials/quota/status
and receive validated Maildirs plus repaired cache rows. Absent SQL users remain
absent and only their stale owned cache entries are removed. A durable reconciled
record permits the subsequent pre-switch guard release.

Malformed records, ownership changes and unsafe existing storage still retain
recovery protection. No messages or SQL users are deleted by reconciliation; no
missing SQL user is recreated speculatively. This supersedes the earlier blanket
refusal of every planned provisioning record.

Validation: seven focused dispatcher/provisioning cases passed across the runs.
The real isolated-SQL tests simulate a lost cache commit and a SQL user that
remains absent, then verify automatic reconciliation, preserved password/disabled
status/quota, correct cache state and released guards. One test assertion was
corrected to accept the driver's empty tuple result. Partial switches and the
customer restore interface remain unfinished.

Supervisor creation and completed-unit retirement now share a per-mail-service
lock. `retire_switch` verifies the persisted operation identifier, refuses a
running worker and only resets a terminal matching unit. It never stops a worker
or restarts mail. This replaces the manual reset in the isolated partial-switch
rollback test, allowing the undo worker to start through the production retirement
helper after journal inspection.

Validation: 28 real supervision/journal tests passed. They verify retirement
cannot race a live caller, remains refused after caller loss while its worker is
still running, refuses another operation's terminal unit and supports the tested
partial-batch rollback. Test supervisor locks are isolated from live storage.
Automatic partial-switch job recovery and the customer interface remain pending.

Rollback continuation now handles an interrupted undo worker. It verifies the
previous undo's job/entries against the original forward journal, confirms the
worker is terminal and mail resumed, then records a new undo journal containing
only original entries whose directories remain exchanged. Already-reverted
entries are omitted. Original and previous journals remain unchanged and all
guards stay owned until final recovery validation.

Validation: 18 journal tests passed. The real systemd fixture now interrupts an
undo after the first of two mailboxes, retires that terminal worker, runs the
generated one-mailbox continuation and verifies both original directory identities
are restored. A running prior worker prevents continuation journal creation.
Automatic job-level rollback orchestration and customer submission remain pending.

Job recovery now orchestrates rollback for partially applied switches. A private
pointer identifies the latest undo journal and validates its linkage to the
original forward journal. Recovery observes that exact worker, creates a fresh
remaining-work journal when necessary, retires the prior terminal unit, persists
the new pointer and launches the supervised undo. Completed original directory
identities and resumed mail service are required before a durable rollback-release
intent permits owned guard removal. The job is then failed with an explicit
rolled-back result; prepared copies and all attempt journals remain retained.

Rollback re-entry also handles interruption after one undo and failed workers that
never exchanged a mailbox. The terminal supervisor is retired even in the latter
case so future jobs are not blocked by its retained failed unit. Eight distinct
dispatcher/rollback cases passed across the runs, including real systemd partial
undo continuation, idempotent completion, guard release and original message
preservation. Customer submission and broader recovery/deployment verification
remain outstanding.
