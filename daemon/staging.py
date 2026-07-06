"""One-click domain-to-staging cloning (Phase 7b feature 6).

Reuses existing provisioning primitives end to end rather than building a
parallel path: handlers_domain.add_domain (Phase 2 feature 4) provisions
staging.<domain> exactly like any other subdomain -- docroot creation
(mode 0750 + the "nobody" ACL grant), the Forgehost-managed-zone DNS A
record, and the OLS vhost render all come for free. Database dump/restore
reuses daemon/backup.py's own mysqldump/mysql helpers (the same ones
daemon/cpanel_import.py already reuses for the identical reason: one
implementation of "dump this DB, import it into that one," not two).
Termination is likewise free: handlers_database.terminate_account_databases
and ols.terminate_vhost (both already existing TERMINATE_HOOKS entries)
tear down a staging domain/database exactly like any other one belonging
to the account -- this module only needs to clean up its own bookkeeping
row on account termination.
"""
from __future__ import annotations

import logging
import os
import pwd
import re
import secrets
import tempfile
from pathlib import Path

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, DatabaseGrant, Domain, StagingEnvironment, utcnow
from shared.validation import validate_domain, validate_username

from daemon import backup, handlers_database, handlers_domain, mariadb, ssl
from daemon.procutil import run
from daemon.wordpress import _php_str

logger = logging.getLogger("forgehostd.staging")


class StagingError(Exception):
    pass


def _staging_domain_for(source_domain: str) -> str:
    return f"staging.{source_domain}"


def _to_dict(row: StagingEnvironment) -> dict:
    return {
        "id": row.id,
        "source_domain": row.source_domain,
        "staging_domain": row.staging_domain,
        "staging_url": f"https://{row.staging_domain}",
        "is_wordpress": row.is_wordpress,
        "db_name": row.db_name,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
    }


def _account_and_domain(username: str, domain_name: str) -> tuple[Account, str]:
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise StagingError(f"account '{username}' not found")
        domain_row = session.scalar(select(Domain).where(Domain.domain == domain_name, Domain.account_id == account.id))
        if domain_row is None:
            raise StagingError(f"domain '{domain_name}' not found for account '{username}'")
        return account, domain_row.docroot


def _is_wordpress(docroot: str) -> bool:
    return os.path.isfile(os.path.join(docroot, "wp-config.php"))


_WP_DEFINE_RE = {
    "DB_NAME": re.compile(r"(define\(\s*['\"]DB_NAME['\"]\s*,\s*)(['\"]).*?\2(\s*\)\s*;)"),
    "DB_USER": re.compile(r"(define\(\s*['\"]DB_USER['\"]\s*,\s*)(['\"]).*?\2(\s*\)\s*;)"),
    "DB_PASSWORD": re.compile(r"(define\(\s*['\"]DB_PASSWORD['\"]\s*,\s*)(['\"]).*?\2(\s*\)\s*;)"),
    "DB_HOST": re.compile(r"(define\(\s*['\"]DB_HOST['\"]\s*,\s*)(['\"]).*?\2(\s*\)\s*;)"),
}
_DB_NAME_RE = re.compile(r"define\(\s*['\"]DB_NAME['\"]\s*,\s*['\"]([^'\"]+)['\"]")


def _source_db_name(source_docroot: str) -> str:
    path = Path(source_docroot) / "wp-config.php"
    content = path.read_text(errors="replace")
    m = _DB_NAME_RE.search(content)
    if not m:
        raise StagingError(f"could not determine the production database name from '{path}'")
    return m.group(1)


def _assert_source_db_owned_by_account(username: str, db_name: str) -> None:
    """Security-audit-2 (Critical): the source database name comes from the
    account's OWN wp-config.php (`_source_db_name`), which the account can
    freely rewrite (file manager / FTP / its own PHP). `backup._dump_database`
    runs `mysqldump` as the MariaDB *admin* (forgehost_daemon), which has
    access to every database on the instance -- so without this check an
    account could point its wp-config's DB_NAME at ANOTHER account's database
    (or the internal `forgehost_mail` schema) and have staging dump it and
    restore it into a database the attacker fully controls: full cross-account
    database exfiltration.

    DatabaseGrant is the authoritative record of which databases an account
    owns (every account DB is created through handlers_database.create_database,
    which writes exactly one grant row) -- require the source DB to be present
    there for this account, rather than trusting the wp-config string or the
    `<username>_` naming convention alone."""
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise StagingError(f"account '{username}' not found")
        owned = session.scalar(
            select(DatabaseGrant).where(DatabaseGrant.db_name == db_name, DatabaseGrant.account_id == account.id)
        )
    if owned is None:
        raise StagingError(
            f"wp-config.php references database '{db_name}', which is not owned by account "
            f"'{username}' -- refusing to clone a database this account does not own into staging"
        )


def _rewrite_staging_wp_config(docroot: str, db_name: str, db_user: str, db_password: str, staging_url: str) -> None:
    """In-place field substitution, not full regeneration: a staging
    clone's salts/table prefix/any custom constants (copied verbatim from
    production) must survive untouched -- only the DB connection consts
    and the site URL genuinely differ on staging. `_php_str` (reused from
    daemon/wordpress.py, the same one daemon/cpanel_import.py's own
    equivalent rewriter already reuses) is what protects a generated
    MariaDB password containing a literal '$' from PHP double-quoted-
    string interpolation corruption (Phase 6b Step 1's documented bug) --
    duplicated as a small regex block here rather than importing
    cpanel_import.py's own copy, so this feature doesn't take on a
    dependency on an unrelated one for a few lines of shared logic."""
    path = os.path.join(docroot, "wp-config.php")
    if not os.path.isfile(path):
        raise StagingError(f"'{path}' does not exist -- cannot rewrite a nonexistent wp-config.php")
    content = Path(path).read_text(errors="replace")
    replacements = {"DB_NAME": db_name, "DB_USER": db_user, "DB_PASSWORD": db_password, "DB_HOST": f"localhost:{settings.mariadb_socket}"}
    missing = []
    for key, pattern in _WP_DEFINE_RE.items():
        value = replacements[key]

        def _sub(m, v=value):
            return f"{m.group(1)}{_php_str(v)}{m.group(3)}"

        content, count = pattern.subn(_sub, content)
        if count == 0:
            missing.append(key)
    if missing:
        # Real bug found by review (not live testing this pass, since live
        # verification is currently blocked -- caught by re-reading this
        # function adversarially instead): unlike
        # daemon/cpanel_import.py's own wp-config rewriter (which only
        # requires *at least one* constant to match, since a partial
        # migration is still better than none), a staging clone needs
        # ALL FOUR DB constants to end up consistent with each other --
        # a wp-config left with the OLD production DB_NAME but the NEW
        # staging DB_USER/DB_PASSWORD would silently produce a staging
        # site that fails to connect at all (the staging db user has no
        # grants on the production database), rather than failing loudly
        # here where the actual cause is obvious. Matches the goal's own
        # "validate config before apply, rollback on failure" rule.
        raise StagingError(
            f"'{path}' is missing an expected define() for: {', '.join(missing)} -- "
            "refusing to leave a staging wp-config.php with inconsistent database credentials"
        )

    # WP_HOME/WP_SITEURL: the standard technique for pointing a cloned
    # WordPress install at a different URL without touching wp_options
    # (which would require assuming a specific table prefix/schema state).
    # define() is first-caller-wins in PHP, so prepending these ahead of
    # anything already in the file (including a production wp-config that
    # itself defines these) guarantees the staging URL takes effect
    # regardless of what the copied-in file does further down.
    home_lines = (
        f"define('WP_HOME', {_php_str(staging_url)});\n"
        f"define('WP_SITEURL', {_php_str(staging_url)});\n"
    )
    content = content.replace("<?php\n", "<?php\n" + home_lines, 1)
    Path(path).write_text(content)


def _copy_files(source_docroot: str, staging_docroot: str, username: str) -> None:
    result = run(["cp", "-a", f"{source_docroot}/.", staging_docroot], timeout=600)
    if not result.ok:
        raise StagingError(f"failed to copy files to the staging docroot: {result.stderr.strip()}")
    pw = pwd.getpwnam(username)
    run(["chown", "-R", f"{pw.pw_uid}:{pw.pw_gid}", staging_docroot], timeout=600, check=True)
    # Same real bug as daemon/cpanel_import.py's own docroot-permission fix
    # (found reviewing that module, confirmed with a real copytree call):
    # a recursive copy can reset the target directory's own mode away from
    # the required 0750 -- reassert mode + the "nobody" ACL grant rather
    # than trust the copy to have preserved Forgehost's permission model.
    handlers_domain.ensure_docroot(username, staging_docroot)


def _dump_and_restore(source_db_name: str, target_db_name: str) -> None:
    fd, dump_path_str = tempfile.mkstemp(suffix=".sql.gz")
    os.close(fd)
    dump_path = Path(dump_path_str)
    try:
        backup._dump_database(source_db_name, dump_path)
        backup._restore_database_dump(target_db_name, dump_path)
    finally:
        dump_path.unlink(missing_ok=True)


def _allocate_staging_database(username: str) -> dict:
    last_error: Exception | None = None
    for attempt in range(5):
        suffix = "stg" if attempt == 0 else f"stg{secrets.token_hex(2)}"
        try:
            return handlers_database.create_database({"username": username, "name": suffix})
        except RuntimeError as exc:
            last_error = exc
            continue
    raise StagingError(f"could not allocate a staging database: {last_error}")


def _clone_database_for_staging(username: str, source_docroot: str, staging_docroot: str, staging_domain: str) -> dict:
    source_db_name = _source_db_name(source_docroot)
    _assert_source_db_owned_by_account(username, source_db_name)
    grant = _allocate_staging_database(username)
    try:
        _dump_and_restore(source_db_name, grant["db_name"])
        _rewrite_staging_wp_config(staging_docroot, grant["db_name"], grant["db_user"], grant["password"], f"https://{staging_domain}")
    except Exception:
        try:
            handlers_database.drop_database({"username": username, "name": grant["db_name"][len(username) + 1 :]})
        except Exception:  # noqa: BLE001
            logger.exception("failed to clean up staging database '%s' after a failed clone", grant["db_name"])
        raise
    return grant


def create_staging(params: dict) -> dict:
    username = validate_username(params["username"])
    source_domain = validate_domain(params["domain"])

    account, source_docroot = _account_and_domain(username, source_domain)

    with write_session() as session:
        existing = session.scalar(select(StagingEnvironment).where(StagingEnvironment.source_domain == source_domain))
        if existing is not None:
            raise StagingError(
                f"a staging environment for '{source_domain}' already exists (id {existing.id}) -- use sync to re-clone it"
            )

    staging_domain = _staging_domain_for(source_domain)
    with write_session() as session:
        if session.scalar(select(Domain).where(Domain.domain == staging_domain)) is not None:
            raise StagingError(f"'{staging_domain}' is already in use as a domain -- cannot create a staging environment")

    # Provisioned exactly like any other subdomain -- docroot creation
    # (mode 0750 + ACL), DNS A record if the parent zone is
    # Forgehost-managed, and OLS vhost render all come from add_domain
    # itself; a failed step below is compensated by removing this same
    # domain, mirroring add_domain's own compensate-on-failure pattern.
    domain_result = handlers_domain.add_domain({"username": username, "domain": staging_domain, "kind": "subdomain"})
    staging_docroot = domain_result["docroot"]

    is_wp = _is_wordpress(source_docroot)
    db_grant = None
    try:
        _copy_files(source_docroot, staging_docroot, username)
        if is_wp:
            db_grant = _clone_database_for_staging(username, source_docroot, staging_docroot, staging_domain)
        try:
            ssl.issue_certificate({"domain": staging_domain})
        except Exception:  # noqa: BLE001 - SSL is best-effort here; the staging site itself is otherwise ready
            logger.exception("SSL issuance failed for staging domain '%s' -- staging site is otherwise ready over plain HTTP", staging_domain)
    except Exception:
        try:
            handlers_domain.remove_domain({"username": username, "domain": staging_domain})
        except Exception:  # noqa: BLE001
            logger.exception("failed to clean up staging domain '%s' after a failed create", staging_domain)
        raise

    with write_session() as session:
        row = StagingEnvironment(
            account_id=account.id,
            source_domain=source_domain,
            staging_domain=staging_domain,
            is_wordpress=is_wp,
            db_name=db_grant["db_name"] if db_grant else None,
            db_user=db_grant["db_user"] if db_grant else None,
        )
        session.add(row)
        session.flush()
        return _to_dict(row)


def sync_staging(params: dict) -> dict:
    """Re-clones from production: files re-copied (fresh, wiping whatever
    was in staging before), and -- for a WordPress site -- the database
    re-dumped/imported into the SAME staging database (mysqldump's default
    `DROP TABLE IF EXISTS` per table means importing over existing tables
    correctly replaces their content, not merely appends to it)."""
    username = validate_username(params["username"])
    source_domain = validate_domain(params["domain"])
    account, source_docroot = _account_and_domain(username, source_domain)

    with write_session() as session:
        row = session.scalar(
            select(StagingEnvironment).where(StagingEnvironment.source_domain == source_domain, StagingEnvironment.account_id == account.id)
        )
        if row is None:
            raise StagingError(f"no staging environment exists for '{source_domain}' -- create one first")
        staging_domain = row.staging_domain
        is_wp = row.is_wordpress
        db_name = row.db_name
        db_user = row.db_user

    with write_session() as session:
        staging_domain_row = session.scalar(select(Domain).where(Domain.domain == staging_domain))
        if staging_domain_row is None:
            raise StagingError(f"staging domain '{staging_domain}' no longer exists -- recreate the staging environment")
        staging_docroot = staging_domain_row.docroot

    _copy_files(source_docroot, staging_docroot, username)

    if is_wp and db_name and db_user:
        source_db_name = _source_db_name(source_docroot)
        _assert_source_db_owned_by_account(username, source_db_name)
        _dump_and_restore(source_db_name, db_name)
        # The staging DB password was never persisted anywhere (this
        # project's "passwords are never stored" rule, applied here the
        # same way backup.py's restore path handles mailbox passwords it
        # can't recover) -- _copy_files just overwrote wp-config.php with
        # production's own copy, so a fresh password is generated and
        # written back in, rather than needing to have remembered the old
        # one.
        new_password = mariadb.generate_password()
        mariadb.set_password(db_user, new_password)
        _rewrite_staging_wp_config(staging_docroot, db_name, db_user, new_password, f"https://{staging_domain}")

    with write_session() as session:
        row = session.scalar(select(StagingEnvironment).where(StagingEnvironment.source_domain == source_domain))
        row.last_synced_at = utcnow()
        return _to_dict(row)


def get_staging(params: dict) -> dict:
    username = validate_username(params["username"])
    source_domain = validate_domain(params["domain"])
    account, _docroot = _account_and_domain(username, source_domain)
    with write_session() as session:
        row = session.scalar(
            select(StagingEnvironment).where(StagingEnvironment.source_domain == source_domain, StagingEnvironment.account_id == account.id)
        )
        if row is None:
            return {"exists": False}
        result = _to_dict(row)
    result["exists"] = True
    return result


def delete_staging(params: dict) -> dict:
    username = validate_username(params["username"])
    source_domain = validate_domain(params["domain"])
    account, _docroot = _account_and_domain(username, source_domain)

    with write_session() as session:
        row = session.scalar(
            select(StagingEnvironment).where(StagingEnvironment.source_domain == source_domain, StagingEnvironment.account_id == account.id)
        )
        if row is None:
            raise StagingError(f"no staging environment exists for '{source_domain}'")
        staging_domain = row.staging_domain
        db_name = row.db_name
        db_suffix = db_name[len(username) + 1 :] if db_name and db_name.startswith(f"{username}_") else db_name
        session.delete(row)

    try:
        handlers_domain.remove_domain({"username": username, "domain": staging_domain})
    except Exception:  # noqa: BLE001
        logger.exception("failed to remove staging domain '%s' during teardown", staging_domain)
    if db_suffix:
        try:
            handlers_database.drop_database({"username": username, "name": db_suffix})
        except Exception:  # noqa: BLE001
            logger.exception("failed to drop staging database '%s' during teardown", db_name)

    return {"source_domain": source_domain, "status": "deleted"}


def terminate_account_staging(account: Account) -> None:
    """TERMINATE_HOOKS entry: the staging domain/database themselves are
    already torn down generically by the existing ols.terminate_vhost/
    handlers_database.terminate_account_databases hooks (a staging
    environment is just an ordinary Domain + DatabaseGrant row scoped to
    this account) -- this only cleans up this feature's own bookkeeping
    row, idempotent/safe even if the account never had one."""
    with write_session() as session:
        rows = session.scalars(select(StagingEnvironment).where(StagingEnvironment.account_id == account.id)).all()
        for row in rows:
            session.delete(row)
