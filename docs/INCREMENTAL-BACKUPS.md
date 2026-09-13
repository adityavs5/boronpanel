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

This module alone does **not** complete the requested backup product. It is not yet connected to the current archive-based backup API or UI. Remaining work includes persistent destinations and reusable policies; account/component/path filters; stable raw database dumps and account metadata; scheduling and crash recovery; progress and selected notification channels; SSH setup and recovery-key export; admin/customer browsing; and safely applying verified full or granular restores.

The service layer must resolve source paths from account ownership rather than accept arbitrary paths from customers. Repository operations need coordination across workers, especially retention/pruning. Account metadata and database dumps must use stable private staging paths so incremental snapshots can reuse unchanged data. Existing archive backups remain available during this integration.

References: [restic repository setup](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html), [backup and filtering](https://restic.readthedocs.io/en/stable/040_backup.html), [restore](https://restic.readthedocs.io/en/stable/050_restore.html).

Latest integration run: **9 passed in 76.07 seconds**, including both local and SSH restores and literal special-character path selection. This validates the storage adapter, not the unfinished job/UI integration.
