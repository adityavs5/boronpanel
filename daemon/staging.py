"""One-click domain-to-staging cloning (Phase 7b feature 6).

Reuses existing provisioning primitives end to end rather than building a
parallel path: handlers_domain.add_domain (Phase 2 feature 4) provisions
staging.<domain> exactly like any other subdomain -- docroot creation
(mode 0750 + the "nobody" ACL grant), the Boron-managed-zone DNS A
record, and the OLS vhost render all come for free. Database dump/restore
reuses daemon/backup.py's own mysqldump/mysql helpers (the same ones
daemon/cpanel_import.py already reuses for the identical reason: one
implementation of "dump this DB, import it into that one," not two).
Termination is likewise free: handlers_database.terminate_account_databases
and ols.terminate_vhost (both already existing TERMINATE_HOOKS entries)
tear down a staging domain/database exactly like any other one belonging
to the account -- this module only needs to clean up its own bookkeeping
row on account termination.

Create and sync build the staging files in a sibling directory, finish the
database and wp-config.php there, and only then swap the tree in, so a
staging site never runs production's wp-config.php against the production
database, and any failure leaves the previous state (or nothing) behind.
The site address is https only when a certificate covers the staging name
(ssl.prepare_https issues one when the name resolves to this server).
"""
from __future__ import annotations

from contextlib import contextmanager
from daemon.database_operations import serialized
import logging
import os
import pwd
import re
import secrets
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, DatabaseGrant, Domain, StagingEnvironment, utcnow
from shared.validation import ConflictError, NotFoundError, ValidationError, validate_domain, validate_username

from daemon import backup, domain_welcome, handlers_database, handlers_domain, mariadb, safeio, ssl
from daemon.procutil import run
from daemon.wordpress import _php_str

logger = logging.getLogger("borond.staging")


class StagingError(ValidationError):
    pass


def _staging_domain_for(source_domain: str) -> str:
    return f"staging.{source_domain}"


def _staging_url(staging_domain: str) -> str:
    """The address the staging site is configured with: https once a valid
    certificate covers the name, otherwise http (see prepare_https)."""
    return ("https://" if ssl.https_ready(staging_domain) else "http://") + staging_domain


def _to_dict(row: StagingEnvironment, staging_url: str) -> dict:
    return {
        "id": row.id,
        "source_domain": row.source_domain,
        "staging_domain": row.staging_domain,
        "staging_url": staging_url,
        "is_wordpress": row.is_wordpress,
        "db_name": row.db_name,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
    }


def _account_and_domain(username: str, domain_name: str) -> tuple[Account, str]:
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise NotFoundError(f"account '{username}' not found")
        domain_row = session.scalar(select(Domain).where(Domain.domain == domain_name, Domain.account_id == account.id))
        if domain_row is None:
            raise NotFoundError(f"domain '{domain_name}' not found for account '{username}'")
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
    content = _read_config(source_docroot)
    m = _DB_NAME_RE.search(content)
    if not m:
        raise StagingError(f"could not determine the production database name from '{path}'")
    return m.group(1)


def _read_config(docroot: str) -> str:
    content = safeio.secure_read_text(docroot, "wp-config.php", 1024 * 1024 + 1)
    if content is None:
        raise StagingError("wp-config.php does not exist or is not a safe regular file")
    if len(content.encode("utf-8")) > 1024 * 1024:
        raise StagingError("wp-config.php exceeds the supported size")
    return content


def _assert_source_db_owned_by_account(username: str, db_name: str) -> None:
    """Security-audit-2 (Critical): the source database name comes from the
    account's OWN wp-config.php (`_source_db_name`), which the account can
    freely rewrite (file manager / FTP / its own PHP). `backup._dump_database`
    runs `mysqldump` as the MariaDB *admin* (boron_daemon), which has
    access to every database on the instance -- so without this check an
    account could point its wp-config's DB_NAME at ANOTHER account's database
    (or the internal `boron_mail` schema) and have staging dump it and
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
            raise NotFoundError(f"account '{username}' not found")
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
    content = _read_config(docroot)
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
    directory = safeio.open_dir_beneath(docroot)
    try:
        owner = os.fstat(directory)
        safeio._replace_file_at_fd(directory, "wp-config.php", content, owner.st_uid, owner.st_gid, 0o640)
    finally:
        os.close(directory)


def _replace_files(source_docroot: str, staging_docroot: str, username: str, *, prepare=None, commit=None) -> None:
    """Make the staging docroot an exact copy of production, so files that
    exist only in staging disappear too (a plain copy over the old tree
    only overwrites). The copy is built in a sibling directory, its
    permissions reasserted and ``prepare(fresh)`` run on it (the staging
    wp-config.php) before it is swapped in; ``commit()`` runs after the
    swap and a failure there swaps the previous tree back. Any failure
    leaves the previous staging files in place. Every filesystem step runs
    as the account itself, inside its own home."""
    pw = pwd.getpwnam(username)
    if pw.pw_uid < 1000 or pw.pw_gid <= 0:
        raise StagingError("refusing staging copy with a system identity")
    staging_docroot = staging_docroot.rstrip("/")
    fresh = f"{staging_docroot}.boron-sync"
    retired = f"{staging_docroot}.boron-retired"

    def as_account(argv: list[str]):
        return run(argv, timeout=600, uid=pw.pw_uid, gid=pw.pw_gid)

    def discard(path: str) -> None:
        as_account(["rm", "-rf", "--one-file-system", "--", path])

    for leftover in (fresh, retired):
        discard(leftover)
    result = as_account(["cp", "-a", "--no-preserve=ownership", "--", f"{source_docroot}/.", fresh])
    if not result.ok:
        discard(fresh)
        raise RuntimeError(f"failed to copy files to the staging docroot: {result.stderr.strip()}")
    try:
        # A recursive copy keeps the production modes; reassert 0750, the
        # web-server ACL and the ACME challenge directory on the new tree
        # (the ACLs move with the directory when it is renamed into place).
        handlers_domain.ensure_docroot(username, fresh)
        if prepare is not None:
            prepare(fresh)
    except Exception:
        discard(fresh)
        raise
    if not as_account(["mv", "-T", "--", staging_docroot, retired]).ok:
        discard(fresh)
        raise RuntimeError("failed to retire the previous staging files")
    if not as_account(["mv", "-T", "--", fresh, staging_docroot]).ok:
        as_account(["mv", "-T", "--", retired, staging_docroot])
        discard(fresh)
        raise RuntimeError("failed to swap in the synced staging files")
    if commit is not None:
        try:
            commit()
        except Exception:
            if as_account(["mv", "-T", "--", staging_docroot, fresh]).ok:
                as_account(["mv", "-T", "--", retired, staging_docroot])
                discard(fresh)
            raise
    discard(retired)


@contextmanager
def _work_dir():
    """A private 0700 scratch directory owned by the daemon, as backups use:
    the database export writes a short-lived MariaDB option file beside the
    dump, and daemon/snapshot_databases.py refuses any directory other users
    can read (a mkstemp file in /tmp made every staging create fail)."""
    from daemon.snapshot_jobs import private_directory

    path = Path(tempfile.mkdtemp(prefix="run-", dir=private_directory("staging")))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _dump_and_restore(source_db_name: str, target_db_name: str, work: Path) -> None:
    dump_path = work / f"{source_db_name}.sql.gz"
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


def _drop_staging_database(username: str, db_name: str) -> None:
    try:
        handlers_database.drop_database({"username": username, "name": db_name[len(username) + 1:]})
    except Exception:  # noqa: BLE001
        logger.exception("failed to clean up staging database '%s' after a failed clone", db_name)


@serialized
def _clone_database_for_staging(username: str, source_docroot: str, staging_docroot: str, staging_url: str) -> dict:
    source_db_name = _source_db_name(source_docroot)
    _assert_source_db_owned_by_account(username, source_db_name)
    grant = _allocate_staging_database(username)
    try:
        _rewrite_staging_wp_config(staging_docroot, grant["db_name"], grant["db_user"], grant["password"], staging_url)
        with _work_dir() as work:
            _dump_and_restore(source_db_name, grant["db_name"], work)
    except Exception:
        _drop_staging_database(username, grant["db_name"])
        raise
    return grant


def _docroot_is_free(username: str, docroot: str) -> bool:
    """The staging docroot may only hold what add_domain provisions in a new
    site root: the ACME challenge directory and the unchanged welcome page.
    Anything else is the customer's, and a failed create must never have to
    choose between leaving its copy behind and deleting their files."""
    if not os.path.lexists(docroot):
        return True
    if os.path.islink(docroot) or not os.path.isdir(docroot):
        return False
    welcome = domain_welcome.is_managed_welcome(username, docroot)
    with os.scandir(docroot) as entries:
        for entry in entries:
            if entry.name == ".well-known" and entry.is_dir(follow_symlinks=False):
                continue
            if entry.name == domain_welcome.NAME and welcome:
                continue
            return False
    return True


def _discard_staging_files(username: str, path: str) -> None:
    """Remove a staging copy (as the account, inside its home)."""
    pw = pwd.getpwnam(username)
    if pw.pw_uid < 1000 or pw.pw_gid <= 0:
        raise StagingError("refusing staging cleanup with a system identity")
    home = os.path.abspath(os.path.join(settings.home_base, username))
    if not os.path.abspath(path).startswith(home + os.sep):
        raise StagingError("staging files must be inside the account home")
    result = run(["rm", "-rf", "--one-file-system", "--", path], timeout=600, uid=pw.pw_uid, gid=pw.pw_gid)
    if not result.ok:
        raise RuntimeError("failed to remove the staging files")


def _https_notice(staging_domain: str, readiness: dict) -> str | None:
    if readiness["https"]:
        return None
    return (f"The staging site uses http://{staging_domain} because {readiness['detail']}. "
            "Issue an SSL certificate for it, then sync to switch it to https.")


def create_staging(params: dict) -> dict:
    username = validate_username(params["username"])
    source_domain = validate_domain(params["domain"])

    account, source_docroot = _account_and_domain(username, source_domain)

    with write_session() as session:
        source_kind = session.scalar(select(Domain.kind).where(Domain.domain == source_domain))
        if source_kind == "parked":
            raise StagingError("a parked domain serves another domain's site -- stage that domain instead")
        if session.scalar(select(StagingEnvironment.id).where(StagingEnvironment.staging_domain == source_domain)) is not None:
            raise StagingError(f"'{source_domain}' is itself a staging environment")
        existing = session.scalar(select(StagingEnvironment).where(StagingEnvironment.source_domain == source_domain))
        if existing is not None:
            raise ConflictError(
                f"a staging environment for '{source_domain}' already exists (id {existing.id}) -- use sync to re-clone it"
            )

    staging_domain = _staging_domain_for(source_domain)
    with write_session() as session:
        if session.scalar(select(Domain).where(Domain.domain == staging_domain)) is not None:
            raise ConflictError(f"'{staging_domain}' is already in use as a domain -- cannot create a staging environment")
    # add_domain's default document root for a subdomain.
    site_root = os.path.join(settings.home_base, username, staging_domain)
    staging_docroot = os.path.join(site_root, "public_html")
    if not _docroot_is_free(username, staging_docroot):
        raise ConflictError(
            f"The folder {staging_domain}/public_html in the home directory already contains files. "
            "Move or delete them, then create the staging site again."
        )
    created_root = not os.path.lexists(site_root)

    # Provisioned exactly like any other subdomain -- docroot creation
    # (mode 0750 + ACL), DNS A record if the parent zone is
    # Boron-managed, and OLS vhost render all come from add_domain
    # itself; a failed step below is compensated by removing this same
    # domain and the files copied for it.
    domain_result = handlers_domain.add_domain({
        "username": username, "domain": staging_domain, "kind": "subdomain", "parent_domain": source_domain,
    })
    staging_docroot = domain_result["docroot"]

    is_wp = _is_wordpress(source_docroot)
    db_grant = None
    try:
        readiness = ssl.prepare_https(staging_domain)
        staging_url = ("https://" if readiness["https"] else "http://") + staging_domain

        def prepare(fresh: str) -> None:
            nonlocal db_grant
            if is_wp:
                db_grant = _clone_database_for_staging(username, source_docroot, fresh, staging_url)

        _replace_files(source_docroot, staging_docroot, username, prepare=prepare)
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
            result = _to_dict(row, staging_url)
    except Exception:
        if db_grant is not None:
            _drop_staging_database(username, db_grant["db_name"])
        try:
            handlers_domain.remove_domain({"username": username, "domain": staging_domain})
        except Exception:  # noqa: BLE001
            logger.exception("failed to clean up staging domain '%s' after a failed create", staging_domain)
        try:
            _discard_staging_files(username, site_root if created_root else staging_docroot)
        except Exception:  # noqa: BLE001
            logger.exception("failed to remove the files of staging domain '%s' after a failed create", staging_domain)
        raise

    notice = _https_notice(staging_domain, readiness)
    if notice:
        result["notice"] = notice
    return result


def sync_staging(params: dict) -> dict:
    """Re-clones from production: files re-copied (fresh, wiping whatever
    was in staging before), and -- for a WordPress site -- the database
    re-dumped/imported into the SAME staging database (mysqldump's default
    `DROP TABLE IF EXISTS` per table means importing over existing tables
    correctly replaces their content, not merely appends to it). The
    staging database is saved first and put back if anything fails before
    the new files are in place."""
    username = validate_username(params["username"])
    source_domain = validate_domain(params["domain"])
    account, _docroot = _account_and_domain(username, source_domain)
    with write_session() as session:
        staging_domain = session.scalar(select(StagingEnvironment.staging_domain).where(
            StagingEnvironment.source_domain == source_domain, StagingEnvironment.account_id == account.id))
    if staging_domain is None:
        raise NotFoundError(f"no staging environment exists for '{source_domain}' -- create one first")
    # Certificate issuance can take minutes: never under the database lock.
    readiness = ssl.prepare_https(staging_domain)
    return _sync_staging_locked(username, source_domain, readiness)


@serialized
def _sync_staging_locked(username: str, source_domain: str, readiness: dict) -> dict:
    account, source_docroot = _account_and_domain(username, source_domain)

    with write_session() as session:
        row = session.scalar(
            select(StagingEnvironment).where(StagingEnvironment.source_domain == source_domain, StagingEnvironment.account_id == account.id)
        )
        if row is None:
            raise NotFoundError(f"no staging environment exists for '{source_domain}' -- create one first")
        staging_domain = row.staging_domain
        is_wp = row.is_wordpress
        db_name = row.db_name
        db_user = row.db_user

    with write_session() as session:
        staging_domain_row = session.scalar(select(Domain).where(Domain.domain == staging_domain, Domain.account_id == account.id))
        if staging_domain_row is None:
            raise StagingError(f"staging domain '{staging_domain}' no longer exists -- recreate the staging environment")
        staging_docroot = staging_domain_row.docroot
        if is_wp and db_name and db_user:
            grant = session.scalar(select(DatabaseGrant).where(
                DatabaseGrant.account_id == account.id, DatabaseGrant.db_name == db_name,
                DatabaseGrant.db_user == db_user,
            ))
            if grant is None:
                raise StagingError("staging database no longer belongs to this account")

    staging_url = ("https://" if readiness["https"] else "http://") + staging_domain
    clone_database = bool(is_wp and db_name and db_user)
    with _work_dir() as work:
        saved = work / "staging-before.sql.gz"
        state = {"imported": False, "password": None}

        def prepare(fresh: str) -> None:
            if not clone_database:
                return
            source_db_name = _source_db_name(source_docroot)
            _assert_source_db_owned_by_account(username, source_db_name)
            # The staging DB password was never persisted anywhere (this
            # project's "passwords are never stored" rule), so a fresh one
            # goes into the new tree's wp-config.php (production's copy) and
            # is set on the database user only once that tree is in place.
            state["password"] = mariadb.generate_password()
            _rewrite_staging_wp_config(fresh, db_name, db_user, state["password"], staging_url)
            backup._dump_database(db_name, saved)
            state["imported"] = True
            _dump_and_restore(source_db_name, db_name, work)

        def commit() -> None:
            if state["password"] is not None:
                mariadb.set_password(db_user, state["password"])

        try:
            _replace_files(source_docroot, staging_docroot, username, prepare=prepare, commit=commit)
        except Exception:
            if state["imported"]:
                try:
                    backup._restore_database_dump(db_name, saved)
                except Exception:  # noqa: BLE001 - the original failure is the actionable one
                    logger.exception("could not put back staging database '%s' after a failed sync", db_name)
            raise

    with write_session() as session:
        row = session.scalar(select(StagingEnvironment).where(StagingEnvironment.source_domain == source_domain))
        row.last_synced_at = utcnow()
        result = _to_dict(row, staging_url)
    notice = _https_notice(staging_domain, readiness)
    if notice:
        result["notice"] = notice
    return result


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
        session.expunge(row)
    result = _to_dict(row, _staging_url(row.staging_domain))
    result["exists"] = True
    return result


@serialized
def delete_staging(params: dict) -> dict:
    """Removes the staging environment: its domain, database and the copy of
    the site's files the panel made for it. Steps that fail after the
    bookkeeping row is gone are reported in ``cleanup_failed``."""
    username = validate_username(params["username"])
    source_domain = validate_domain(params["domain"])
    account, _docroot = _account_and_domain(username, source_domain)

    with write_session() as session:
        row = session.scalar(
            select(StagingEnvironment).where(StagingEnvironment.source_domain == source_domain, StagingEnvironment.account_id == account.id)
        )
        if row is None:
            raise NotFoundError(f"no staging environment exists for '{source_domain}'")
        staging_domain = row.staging_domain
        db_name = row.db_name
        db_suffix = db_name[len(username) + 1 :] if db_name and db_name.startswith(f"{username}_") else db_name
        staging_row = session.scalar(select(Domain).where(Domain.domain == staging_domain, Domain.account_id == account.id))
        staging_docroot = staging_row.docroot if staging_row is not None else None
        if staging_row is not None:
            # remove_domain refuses while an app or parked alias uses the
            # name; refuse before the row and database go, not halfway.
            handlers_domain.require_no_dependents(session, account.id, staging_domain)
        session.delete(row)

    failed = []
    removed = False
    try:
        removal = handlers_domain.remove_domain({"username": username, "domain": staging_domain})
        removed = removal.get("status") == "removed"
        failed += removal.get("cleanup_failed", [])
    except Exception:  # noqa: BLE001
        logger.exception("failed to remove staging domain '%s' during teardown", staging_domain)
        failed.append("staging domain")
    if db_suffix:
        try:
            handlers_database.drop_database({"username": username, "name": db_suffix})
        except Exception:  # noqa: BLE001
            logger.exception("failed to drop staging database '%s' during teardown", db_name)
            failed.append("staging database")
    # Only the copy in the panel's own staging location, and only once no
    # vhost serves it any more.
    default_docroot = os.path.join(settings.home_base, username, staging_domain, "public_html")
    if removed and staging_docroot == default_docroot:
        try:
            _discard_staging_files(username, staging_docroot)
        except Exception:  # noqa: BLE001
            logger.exception("failed to remove the files of staging domain '%s'", staging_domain)
            failed.append("staging files")

    result = {"source_domain": source_domain, "status": "deleted"}
    if failed:
        result["cleanup_failed"] = failed
    return result


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
