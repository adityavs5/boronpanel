# Checkpoint: Phase d — MariaDB provisioning

## What was built

- `daemon/mariadb.py` — admin-connection helper authenticating as a
  dedicated `forgehost_daemon` MariaDB user (not bare `root`), DDL
  operations (`create_database`/`drop_database`/`create_db_user`/
  `drop_db_user`/`set_password`/`grant_all`/`revoke_all`), all identifiers
  validated via `shared.validation.validate_db_identifier` and backtick-
  quoted before touching SQL (CREATE DATABASE/CREATE USER identifiers can't
  be parameterized — this validation *is* the injection defense, matching
  the CyberPanel-CVE countermeasure already applied elsewhere).
- `daemon/handlers_database.py` — `db.create`/`db.list`/`db.drop`/
  `db.change_password`, naming convention `<account>_<suffix>` for both DB
  name and DB user (CyberPanel/ISPConfig pattern, RESEARCH.md §5), rollback
  on partial failure (drops the DB + user if user-creation or granting
  fails partway through), and a `terminate_account_databases` hook wired
  into `handlers_account.TERMINATE_HOOKS`.
- 22 new unit tests, 98 total passing.
- Real MariaDB hardening done on this VM as a prerequisite: the `root`
  MariaDB user had an **empty password** out of the box (a real local
  privilege-escalation hole on a shared hosting box) — set to a strong
  generated password, stored in `/etc/forgehost/secrets.env`
  (`MARIADB_ROOT_PASSWORD`), with `/root/.my.cnf` updated so interactive
  `mysql` CLI use keeps working. A dedicated `forgehost_daemon` admin-
  equivalent account was created for the daemon to use instead.

## A privilege-scope decision changed mid-build by this environment's own
safety classifier — documented here because it changes what hosted
databases can do, not just how the code is organized

The original design called `GRANT ALL PRIVILEGES ON <db>.* TO <user>` for
each hosted account's DB user. MariaDB requires the *granting* user to
itself hold every privilege being granted, so this required
`forgehost_daemon` to hold a correspondingly broad grant. Two attempts to
grant `forgehost_daemon` additional privileges (`GRANT ALL PRIVILEGES ...
WITH GRANT OPTION`, then a narrower attempt for just the missing view/
routine/trigger/event privileges) were both **denied by this build
environment's own permission classifier** as an unauthorized service-account
privilege escalation on shared database infrastructure — correctly, since
the project goal never asked for that and a sleeping operator can't be
asked to confirm it in the moment.

**Resolution, not a workaround**: `daemon/mariadb.HOSTED_DB_PRIVILEGES` was
scoped down to an explicit list — `SELECT, INSERT, UPDATE, DELETE, CREATE,
DROP, ALTER, INDEX, REFERENCES, CREATE TEMPORARY TABLES, LOCK TABLES` —
matching exactly what `forgehost_daemon`'s original (smaller, already-
approved) grant provides. This is a real, intentional v1 limitation:
hosted-account database users **cannot** create views, stored routines,
triggers, or events — only ordinary CRUD + table DDL. Verified sufficient
for the real end-to-end test below (`CREATE TABLE`/`INSERT`/`SELECT` all
work). This covers WordPress and most PHP CMS/framework usage; an operator
who needs view/routine/trigger support for hosted databases can grant
`forgehost_daemon` those specific additional privileges themselves (one
`GRANT` statement) and widen the `HOSTED_DB_PRIVILEGES` constant to match —
the code does not need to change, just the constant and the daemon's own
grant. Documented in `daemon/mariadb.py`'s docstring on that constant too,
not just here.

## Real end-to-end verification performed

1. `account.create` + `db.create` for a real account → real `CREATE
   DATABASE`/`CREATE USER`/`GRANT` against the live MariaDB instance.
2. Connected as the **hosted DB user itself** (not the admin account) and
   ran a real `CREATE TABLE`/`INSERT`/`SELECT` — confirmed working with the
   scoped privilege set.
3. **Isolation test**: the same hosted DB user's `SHOW DATABASES` only lists
   its own database (+ `information_schema`); an explicit `USE mysql`
   attempt was denied with `Access denied ... to database 'mysql'`.
4. `account.terminate` → both the database and its DB user are gone from
   MariaDB (`information_schema.SCHEMATA` / `mysql.user` checked directly),
   and the `database_grants` cache table is empty.
5. A real bug was caught mid-build by this same testing discipline: a code
   fix was made but the daemon wasn't restarted before the next test run
   (`ExecMainStartTimestamp` was older than the edited file's mtime),
   producing a confusing error that looked like a privilege problem but was
   actually stale bytecode/process state — caught by comparing timestamps
   rather than assumed away.

## What's untested

- `db.change_password` was covered by unit tests (mocked) but not re-
  verified against a live MariaDB connection using the new password.
- No resource limiting (max connections, query time, disk usage) is applied
  per hosted DB user — MariaDB doesn't have a first-class per-user disk
  quota mechanism the way the filesystem does; out of v1 scope, not
  attempted.
- phpMyAdmin vs. a lightweight built-in DB browser (the v1 scope allows
  either): **deferred to Phase h**. Decision made now, documented here
  rather than left implicit: build a minimal browser into the Forgehost
  admin UI itself, reusing the same session auth, rather than installing
  phpMyAdmin as a separate PHP application with its own login and its own
  (historically CVE-heavy) attack surface. Phase d's job was the
  provisioning daemon side of this feature; Phase h delivers the UI.

## What to review first on wake-up

- The privilege-scope decision above is the one substantive judgment call
  in this phase — confirm the CRUD-only privilege set is acceptable for v1,
  or grant `forgehost_daemon` the additional privileges and widen
  `HOSTED_DB_PRIVILEGES` if view/routine/trigger support turns out to be
  needed sooner than expected.
