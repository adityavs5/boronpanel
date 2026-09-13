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
