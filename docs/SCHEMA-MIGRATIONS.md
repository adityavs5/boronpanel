# Database and configuration migration policy

Boron supports the current release and a guaranteed rollback to the immediately previous release (N-1). N-2 rollback is best effort. The control-plane database is SQLite and is migrated by the ordered registry in `shared/migrations.py`.

## Revision contract

- Revision numbers are contiguous integers. Revision 0 is the automatically detected baseline for installations that predate the ledger.
- Every revision declares `additive` or `breaking` compatibility and records its name, checksum, application version, and application time in `schema_migrations`.
- `control_plane_metadata` records the effective `schema_version` and the last `app_version` that completed migration startup.
- A revision must be idempotent. If the process exits between SQLite DDL and its ledger insert, rerunning the revision must safely converge.
- Additive means older code can ignore the change: a nullable/defaulted column, a new table, or a new non-conflicting index. It must not reinterpret an existing value.
- Breaking means older code cannot safely operate on the resulting database. It requires expand then contract across two releases.

## Expand then contract

For a rename, type change, constraint tightening, or removal:

1. Release N adds the new representation. Code reads both representations and writes both. The revision is additive.
2. Release N+1 continues dual read/write, verifies backfill completion, and stops depending on the old representation. Rollback to N remains safe.
3. Release N+2 may remove the old representation in a breaking revision. Its release manifest raises the minimum supported schema/code range, and rollback to code that cannot understand that revision is refused.

Never add and drop or rename the same column in one release. `scripts/check_schema_migrations.py` enforces this in CI.

## Configuration documents

`/etc/boron/boron.toml` has `config_schema_version`. Missing version means legacy v0. `shared/config.py` migrates known renamed keys in memory for both processes, warns about unknown keys without discarding them, and borond persists only the schema/key-name edits with the original owner and mode. Secret files are separate and never rewritten by configuration migration.

## Developer checklist

- [ ] Add a contiguous `Revision` entry and choose the compatibility class based on the definitions above.
- [ ] Keep the upgrade function safe to run after partial completion and safe to run twice.
- [ ] Add a legacy-schema fixture containing real data and prove it upgrades without loss.
- [ ] Prove the N-1 code can still start and read/write representative data after every additive revision.
- [ ] For a breaking revision, implement and ship the expand and dual-write releases first; document their minimum release versions.
- [ ] Update `CURRENT_SCHEMA_VERSION` and `schema-compat.json` together.
- [ ] Add or update updater compatibility tests, including a refused rollback for a breaking revision.
- [ ] Do not log row values, configuration secrets, or migration SQL parameters.
- [ ] Run `scripts/check_schema_migrations.py`, the complete backend suite, and the staged update/rollback tests.
- [ ] Document operator recovery and whether a database backup must be restored when rolling back.
