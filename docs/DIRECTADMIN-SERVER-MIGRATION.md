# DirectAdmin server pull migration

Included in the 1.6.0 release candidate. Offline importer regression tests and the
frontend production build pass. Real DirectAdmin source-server acceptance remains
an operator test: no source-server credentials were supplied for that check.

## Admin workflow

Account migrations → From DirectAdmin server → connect → select accounts → import.
Use a DirectAdmin administrator password/login key over verified HTTPS, or root
SSH with a console-verified SHA256 host-key fingerprint. Root mode requires a
DirectAdmin version with `da api-url` and curl. No source SSH setup is necessary
for the admin API mode. API/login-key restrictions must permit listing all users,
user impersonation, site backups, and reading/downloading the user's backup files.

The importer queues one account job per selection, creates a source user backup,
waits for a new/changed archive to stabilize, streams it into root-only staging,
and uses the existing bounded extraction and account import pipeline. Archive
size and metadata are checked again after transfer. Incomplete archives fail
extraction before creating an account. Existing destination accounts are never
merged or overwritten; a different target username can be selected.

Source passwords are held in worker memory only, redacted from audit parameters,
and not stored in jobs. Daemon restart marks interrupted remote jobs failed and
requires reconnecting. Created source backups remain on the DirectAdmin server.
Local staging is removed on completion or ordinary failure. Abrupt process death
can leave root-only staging requiring operator cleanup.

The source remains live. This is a snapshot migration, not continuous replication:
plan a write freeze/final migration before DNS cutover. Do not run simultaneous
backup jobs for the same source account while migrating. DNS/nameservers are not
automatically switched. Existing archive-adapter limitations still apply: this is
not a promise that every DA setting, forwarder, mailing list or credential is
translated. Review per-item results and verify all services before cutover.

## MySQL / MariaDB compatibility

Use logical SQL dumps, never raw database data directories or system grant tables.
Preflight reads the actual destination version, supported collations and engines,
and source version banners when present, before creating the destination account.

Strict mode rejects detected unsupported collations/engines, foreign DEFINERs and
replication metadata. Opt-in adaptation maps a small explicit set of unsupported
modern UTF-8 collations to supported equivalents and rebinds object definers to
CURRENT_USER. Quoted application data and ordinary comments are not rewritten.
Alternative SQL quote modes disable automatic rewriting. Collation mappings may
change comparisons and uniqueness; duplicate-key errors must be resolved, not
ignored. SQL mode is not relaxed and mysql never receives --force.

Unknown SQL features can still fail during restore (JSON/generated columns,
functions, version-specific SQL syntax, routine privileges, zero dates, etc.).
Preflight is not a full SQL parser or proof of compatibility. Database errors are
reported per item; a partially loaded database may remain for diagnosis. Do not
switch DNS until it is resolved. Currently dumps are read in memory, matching the
existing importer; very large databases require sufficient destination RAM.

Destination database logins are newly provisioned. WordPress config rewrites reuse
one credential per imported database, including sites sharing a database.
Non-WordPress application connection settings require manual adjustment.

## Deferred acceptance checklist

- Real DA administrator and root SSH flows, current and older supported versions.
- Empty backup directory, existing same-day backup, slow backup, interrupted
  transfer, authentication failure, certificate/key mismatch, size limits.
- MySQL 5.7/8.x and MariaDB sources; strict rejection and opt-in mappings;
  routines/views/triggers, large datasets, shared WordPress database credentials.
- Restart recovery, existing-account conflicts, multi-account partial queue failures.

## Protocol references

- [DirectAdmin API authentication and impersonation](https://docs.directadmin.com/developer/api/)
- [DirectAdmin root API URL](https://docs.directadmin.com/directadmin/general-usage/directadmin-binary.html)
- [Site backup API](https://docs.directadmin.com/changelog/version-1.24.3.html)
- [File manager listing and download API](https://docs.directadmin.com/changelog/version-1.27.2.html)
- [MariaDB compatibility matrix](https://mariadb.com/docs/server/server-management/install-and-upgrade-mariadb/migrating-to-mariadb/moving-from-mysql/mysql-to-mariadb-compatibility-matrix)
- [MySQL logical dump and GTID options](https://dev.mysql.com/doc/refman/8.0/en/mysqldump.html)
