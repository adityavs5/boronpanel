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

The storage and persistent job APIs do **not** yet complete the requested backup product. The service described below now provides destinations, policies, filters, raw database dumps, scheduling, recovery, retention, notifications and account-scoped browsing. Remaining work includes the admin/customer management screens, safely applying verified full or granular restores, broader account configuration recovery, and live deployment and verification.

The service resolves source paths from account ownership rather than accepting arbitrary customer paths. Account and repository locks coordinate its workers, including retention/pruning. Account metadata and database dumps use stable private staging paths so incremental snapshots can reuse unchanged data. Coordination with legacy archive restores and other account-changing operations still needs a final audit before deployment. Existing archive backups remain available during this integration.

References: [restic repository setup](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html), [backup and filtering](https://restic.readthedocs.io/en/stable/040_backup.html), [restore](https://restic.readthedocs.io/en/stable/050_restore.html).

Latest integration run: **9 passed in 76.07 seconds**, including both local and SSH restores and literal special-character path selection. This validates the storage adapter, not the unfinished job/UI integration.

## Persistent job service

`daemon/snapshot_jobs.py` adds destinations, policies and per-account runs in separate SQLite tables. Secrets stay in the private repository directory, outside the API-readable control database. Administrators create a destination, install its generated SSH public key remotely when applicable, supply a trusted SSH server host key, and initialize the repository. The explicit recovery-key endpoint is admin-only and returns `Cache-Control: no-store`.

Policies select accounts (an empty selection includes all active accounts), excluded accounts, files/databases/mail/config, home-relative included paths, exclusion patterns, full/incremental mode, retention count, and email/webhook channels. Each queued run freezes those options. Hourly/daily/weekly schedules use the existing hourly scheduler; manual jobs run on demand. Repeated scheduling and busy accounts do not enqueue duplicate account work.

Workers export raw SQL with a consistent transaction and stable private paths, then snapshot raw home/mail files and metadata. Account and repository file locks coordinate daemon and scheduled processes. Startup requeues pending work and identifies interrupted running work by checking its account lock. Retention marks old history entries expired. Notifications report dispatch or suppression; SMTP acceptance/webhook queueing is not proof of delivery.

Admin APIs are under `/api/v1/backups/snapshots`; customer history and browsing are under `/api/v1/accounts/{username}/backups/snapshots/runs`. Customer history omits global policy account lists and notification selections. The management UI, applying staged restores, broader metadata recovery and live deployment remain unfinished; these endpoints are not yet a shipped replacement for the archive backup screens.

Persistent job verification: **69 passed** across the new job tests, existing archive backup tests and RPC tests. After the duplicate-worker notification fix, **12 job tests passed** again. Logs: `/root/boron-setup/snapshot-job-tests.log` and `/root/boron-setup/snapshot-job-final-tests.log`. The only warning was the existing FastAPI/Starlette TestClient dependency deprecation.

## Management screens

The admin Backup Manager at `/app/backup-jobs` provides destination setup (local or SSH), public-key copying, explicit recovery-key reveal/download, job creation/editing, all/selected/excluded account filters, component and path filters, schedule/mode/retention selection, selected notification channels, manual runs and run history. Jobs and destinations have visible clickable names and management controls. Both dashboard theme grids and fuzzy search include the new page.

Customers see scheduled recovery points above their existing on-demand archive backups. Shared details show status, processed files, stored data, components, notification dispatch results and account-scoped browsing. Root shortcuts identify account files, email and database/configuration exports. Long forms keep their action buttons visible while their body scrolls. Admin and customer routes remain role-scoped.

The new admin page is loaded on demand (5.79 kB gzip); shared history is 2.46 kB gzip in this build. No new fonts, image assets or frontend packages were added. These sizes are build evidence, not a measured end-to-end performance benchmark.

Applying restored snapshot contents remains unfinished, and this new backup UI has not yet been deployed to the live panel. Existing archive restore functionality is retained.

UI verification: production build passed; **6 browser tests passed** across admin light/dark themes and customer browsing, followed by **2 final customer tests** that also submitted the existing full archive-backup form. **12 backend tests passed** for the updated history and browsing behavior. Evidence: `/root/boron-setup/backup-ui-build.log`, `backup-ui-browser.log`, `backup-ui-customer-final.log`, `backup-ui-api.log`, and screenshots in `/root/boron-setup/backup-ui-proof`. Browser flows use mocked API data; they are not evidence of live deployment or completed snapshot restore application.
