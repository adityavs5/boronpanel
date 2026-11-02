"""phpMyAdmin single-signon auto-login, scoped to exactly one database
(Phase 3 feature 3).

Architecture: phpMyAdmin's own `signon` auth_type (its documented
mechanism for exactly this "hand phpMyAdmin a pre-authenticated session"
use case) rather than a fragile scrape of its login form. A short-lived
(15 min default), single-use, server-generated token maps to a freshly
created MariaDB user, scoped to exactly one database via the same
`HOSTED_DB_PRIVILEGES` grant every hosted database already uses (never
the real db_user's own password, which Boron never stores -- see
daemon/mariadb.py) -- redeeming the token logs into phpMyAdmin as that
throwaway user, which physically cannot touch any other database.

The signon redemption itself (`templates/pma_signon.php.j2`, deployed by
bootstrap_pma()) runs as an ordinary PHP script under phpMyAdmin's own
docroot/extProcessor (www-data), with no access to Boron's control-
plane DB or RPC socket. State is handed to it via a small per-token JSON
file under settings.pma_token_dir -- a directory deliberately created
OUTSIDE /var/lib/boron (which shared/db.py locks to
root:boron-api), a lesson learned the hard way while building Phase 3
feature 2's WordPress installer (see CHECKPOINT-phase3-2.md): a file
under a hosting-account-or-www-data-inaccessible tree is simply unusable
by anything that isn't borond/boron-api itself.
"""
from __future__ import annotations

import datetime as dt
import grp
import hashlib
import json
import logging
import os
import pwd
import re
import secrets
import threading
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, DatabaseGrant, Domain, PmaToken, utcnow
from shared.validation import NotFoundError, validate_username, validate_domain, ValidationError

from daemon import appcrypto, database_operations, db_governor, mariadb
from daemon.handlers_database import _resolve_existing_db_name

logger = logging.getLogger("borond.pma")

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
_TOKEN_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
PMA_RESOURCE_WINDOW_SECONDS = 60 * 60


class PmaError(Exception):
    pass


_EPHEMERAL_USER_RE = re.compile(r"^boronpma_[0-9a-f]+_[0-9a-f]{16}$")


def ephemeral_user_name(account_id: int, db_name: str) -> str:
    """The stable single-database sign-on login for one account/database."""
    stable_suffix = hashlib.sha256(f"{account_id}:{db_name}".encode()).hexdigest()[:16]
    return f"boronpma_{account_id:x}_{stable_suffix}"


def is_available() -> bool:
    """True once Server Setup published phpMyAdmin and its sign-on script."""
    if not settings.pma_hostname:
        return False
    try:
        return (Path(settings.pma_docroot) / "boron_signon.php").is_file()
    except OSError:
        return False


def revoke_tokens(account_id: int, db_name: str | None = None) -> int:
    """Drop sign-on logins, token files and rows for an account (or one of
    its databases). Database deletion and account termination call this so
    no ephemeral principal keeps native grants on a name that is reused.
    Raises on the first login that cannot be dropped; its row is retained
    so a retry (or scripts/pma_token_cleanup.py) can finish revocation."""
    with write_session() as session:
        query = select(PmaToken).where(PmaToken.account_id == account_id)
        if db_name is not None:
            query = query.where(PmaToken.db_name == db_name)
        rows = [(row.id, row.token_hash, row.ephemeral_db_user, row.db_name)
                for row in session.scalars(query).all()]
    # A principal whose row was lost is retired when its name is reused
    # (retire_orphaned_principals), so only registered logins are dropped.
    users = {(user, name) for _id, _hash, user, name in rows}
    for user, _name in sorted(users):
        mariadb.drop_db_user(user)
    try:
        token_dir = _validate_token_dir_path()
    except PmaError:
        token_dir = None
    for token_id, token_hash, _user, _name in rows:
        if token_dir is not None:
            try:
                _token_file_for_hash(token_hash, token_dir).unlink(missing_ok=True)
            except (PmaError, OSError):
                logger.warning("could not remove phpMyAdmin token file for row %s", token_id)
        with write_session() as session:
            row = session.get(PmaToken, token_id)
            if row is not None:
                session.delete(row)
    return len(users)


def retire_orphaned_principals(db_name: str) -> int:
    """Drop sign-on logins still granted on an absent database name.

    Only principals that match the reserved sign-on pattern and that no
    registered customer login or live token references are removed.
    """
    from shared.models import DatabaseUser
    grantees = mariadb.database_grantees(db_name)
    candidates = [(user, host) for user, host in grantees
                  if host == "localhost" and _EPHEMERAL_USER_RE.fullmatch(user)]
    if not candidates:
        return 0
    removed = 0
    with write_session() as session:
        names = [user for user, _host in candidates]
        live_tokens = set(session.scalars(select(PmaToken.ephemeral_db_user).where(
            PmaToken.ephemeral_db_user.in_(names), PmaToken.db_name != db_name)).all())
        registered = set(session.scalars(select(DatabaseUser.db_user).where(
            DatabaseUser.db_user.in_(names))).all())
        stale_rows = [row.id for row in session.scalars(select(PmaToken).where(
            PmaToken.ephemeral_db_user.in_(names), PmaToken.db_name == db_name)).all()]
    for user, host in candidates:
        if user in live_tokens or user in registered:
            continue
        mariadb.drop_db_user(user, host)
        removed += 1
    with write_session() as session:
        for row_id in stale_rows:
            row = session.get(PmaToken, row_id)
            if row is not None:
                session.delete(row)
    return removed


def _validate_token_dir_path() -> Path:
    path = Path(settings.pma_token_dir)
    if not path.is_absolute():
        raise PmaError("phpMyAdmin token directory must be an absolute path")
    if path.exists() and path.resolve() != path:
        raise PmaError("phpMyAdmin token directory must not be a symbolic link")
    return path


def _www_data_gid() -> int | None:
    try:
        return grp.getgrnam("www-data").gr_gid
    except KeyError:
        logger.warning("www-data group not found; pma token files left root-only, signon will fail")
        return None


def _token_dir() -> Path:
    """mkdir(mode=0o770) alone sets permission *bits*, not group
    *ownership* -- a directory borond (root) creates defaults to
    group "root", which www-data isn't a member of, making the mode bits
    irrelevant. Confirmed live: token files were written correctly but
    every signon attempt still 403'd with "token not found", because
    www-data could not even traverse into the directory to read them.
    Explicit chown here, every call (idempotent, cheap), same pattern
    server.py's amain() already uses for /run/boron's socket dir."""
    path = _validate_token_dir_path()
    path.mkdir(parents=True, exist_ok=True, mode=0o770)
    if path.resolve() != path:
        raise PmaError("phpMyAdmin token directory must not traverse symbolic links")
    gid = _www_data_gid()
    if gid is not None:
        os.chown(path, 0, gid)
    os.chmod(path, 0o770)
    return path


def _token_file_for_hash(token_hash: str, token_dir: Path | None = None) -> Path:
    if not isinstance(token_hash, str) or not _TOKEN_HASH_RE.fullmatch(token_hash):
        raise PmaError("invalid phpMyAdmin token hash")
    return (token_dir or _token_dir()) / f"{token_hash}.json"


def _write_token_file(token_file: Path, payload: dict) -> None:
    token_dir = token_file.parent
    gid = _www_data_gid()
    dir_fd = os.open(token_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    created = False
    name = token_file.name
    try:
        fd = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o640,
            dir_fd=dir_fd,
        )
        created = True
        try:
            data = memoryview(json.dumps(payload).encode())
            while data:
                written = os.write(fd, data)
                if written <= 0:
                    raise OSError("short write while creating phpMyAdmin token")
                data = data[written:]
            if gid is not None:
                os.fchown(fd, 0, gid)
            os.fchmod(fd, 0o640)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(dir_fd)
    except Exception:
        if created:
            try:
                os.unlink(name, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
        raise
    finally:
        os.close(dir_fd)


@database_operations.serialized
def create_token(params: dict) -> dict:
    username = validate_username(params["username"])
    db_name = _resolve_existing_db_name(username, params["name"])
    pma_hostname = validate_domain(settings.pma_hostname) if settings.pma_hostname else ""

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise NotFoundError(f"account '{username}' not found")
        grant = session.scalar(
            select(DatabaseGrant).where(DatabaseGrant.account_id == account.id, DatabaseGrant.db_name == db_name)
        )
        if grant is None:
            raise NotFoundError(f"database '{db_name}' not found for account '{username}'")
        account_id = account.id
    if not pma_hostname or not is_available():
        # Nothing could redeem a token: never mint a MariaDB login for it.
        return {"token": None, "db_name": db_name, "pma_url": None, "available": False,
                "expires_in_seconds": 0}

    with write_session() as session:
        existing_rows = session.scalars(
            select(PmaToken).where(
                PmaToken.account_id == account_id,
                PmaToken.db_name == db_name,
            ).order_by(PmaToken.id.desc())
        ).all()
        now = utcnow()
        existing = next((row for row in existing_rows
                         if (row.expires_at.replace(tzinfo=dt.timezone.utc)
                             if row.expires_at.tzinfo is None else row.expires_at) > now
                         and row.credential_enc), None)
        existing_data = None if existing is None else {
            "id": existing.id,
            "token_hash": existing.token_hash,
            "user": existing.ephemeral_db_user,
            "password": appcrypto.decrypt_secret(existing.credential_enc or ""),
        }
        stale_data = [
            (row.id, row.token_hash, row.ephemeral_db_user)
            for row in existing_rows if existing is None or row.id != existing.id
        ]

    for stale_id, stale_hash, stale_user in stale_data:
        if not existing_data or stale_user != existing_data["user"]:
            mariadb.drop_db_user(stale_user)
        try:
            _token_file_for_hash(stale_hash).unlink(missing_ok=True)
        except PmaError:
            logger.warning("invalid phpMyAdmin token hash in stale row %s", stale_id)
        with write_session() as session:
            stale = session.get(PmaToken, stale_id)
            if stale is not None:
                session.delete(stale)

    ephemeral_user = ephemeral_user_name(account_id, db_name)
    reuse = bool(
        existing_data
        and existing_data["user"] == ephemeral_user
        and existing_data["password"]
        and mariadb.user_exists(ephemeral_user)
    )
    ephemeral_password = existing_data["password"] if reuse else mariadb.generate_password()
    created_user = False
    if not reuse:
        # Retire a legacy/random or incomplete principal before publishing the
        # stable per-account/database principal. Reissuing a one-click token
        # during the same MariaDB resource window then reuses its counters.
        if existing_data:
            mariadb.drop_db_user(existing_data["user"])
            _token_file_for_hash(existing_data["token_hash"]).unlink(missing_ok=True)
            with write_session() as session:
                stale = session.get(PmaToken, existing_data["id"])
                if stale is not None:
                    session.delete(stale)
        if mariadb.user_exists(ephemeral_user):
            mariadb.drop_db_user(ephemeral_user)
        mariadb.create_db_user(ephemeral_user, ephemeral_password)
        created_user = True
    token_file = None
    try:
        if created_user:
            mariadb.grant_exact_database(db_name, ephemeral_user)
            # Apply native limits once per stable resource window. Recreating
            # or re-limiting a random user on every click refreshes the
            # account's hourly query/update/connection allowance.
            db_governor.apply_new_user(account_id, ephemeral_user, "localhost")

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires_at_epoch = int(time.time()) + settings.pma_token_ttl_seconds

        token_file = _token_file_for_hash(token_hash)
        _write_token_file(
            token_file,
            {
                "db_user": ephemeral_user,
                "db_password": ephemeral_password,
                "db_name": db_name,
                "expires_at": expires_at_epoch,
            },
        )

        with write_session() as session:
            row = session.get(PmaToken, existing_data["id"]) if reuse else None
            if row is None:
                row = PmaToken(
                    token_hash=token_hash,
                    account_id=account_id,
                    db_name=db_name,
                    ephemeral_db_user=ephemeral_user,
                    credential_enc=appcrypto.encrypt_secret(ephemeral_password),
                    expires_at=utcnow() + dt.timedelta(seconds=PMA_RESOURCE_WINDOW_SECONDS),
                )
                session.add(row)
            else:
                row.token_hash = token_hash
                row.expires_at = utcnow() + dt.timedelta(seconds=PMA_RESOURCE_WINDOW_SECONDS)
        if reuse and existing_data["token_hash"] != token_hash:
            _token_file_for_hash(existing_data["token_hash"]).unlink(missing_ok=True)
    except Exception:
        if token_file is not None:
            token_file.unlink(missing_ok=True)
        if created_user:
            mariadb.drop_db_user(ephemeral_user)
        raise

    pma_url = f"https://{pma_hostname}/boron_signon.php?token={token}"

    return {
        "token": token,
        "db_name": db_name,
        "pma_url": pma_url,
        "available": True,
        "expires_in_seconds": settings.pma_token_ttl_seconds,
    }


@database_operations.serialized_worker
def cleanup_expired_tokens() -> int:
    """Called periodically (scripts/pma_token_cleanup.py, system cron,
    mirroring usage_snapshot.py/backup_scheduler.py's established
    pattern): drops the ephemeral MariaDB user + any still-present token
    file for every PmaToken row past its expiry, whether or not it was
    ever actually redeemed (a redeemed token's file is already gone --
    signon.php deletes it on first use -- but the ephemeral MariaDB user
    still needs dropping either way, since nothing else ever does that).
    Returns the number of tokens cleaned up."""
    token_dir = _token_dir()
    with write_session() as session:
        expired = session.scalars(select(PmaToken).where(PmaToken.expires_at < utcnow())).all()
        rows = [(t.id, t.token_hash, t.ephemeral_db_user) for t in expired]

    cleaned = 0
    for token_id, token_hash, ephemeral_user in rows:
        try:
            mariadb.drop_db_user(ephemeral_user)
        except Exception:
            logger.exception("failed to drop ephemeral pma MariaDB user '%s'", ephemeral_user)
            continue  # Retain the row so the next cleanup retries credential revocation.
        try:
            token_file = _token_file_for_hash(token_hash, token_dir)
        except PmaError:
            logger.warning("skipping invalid phpMyAdmin token hash for cleanup row %s", token_id)
        else:
            token_file.unlink(missing_ok=True)
        with write_session() as session:
            row = session.get(PmaToken, token_id)
            if row is not None:
                session.delete(row)
        cleaned += 1

    return cleaned


def _config_path(docroot: Path) -> Path:
    # Distribution packages may relocate configuration outside the webroot.
    # Read the literal package declaration without executing PHP.
    vendor = docroot / "libraries/vendor_config.php"
    if vendor.is_file():
        match = re.search(r"['\"]configFile['\"]\s*=>\s*['\"](/[^'\"\r\n]+)['\"]", vendor.read_text())
        if match:
            return Path(match.group(1))
    return docroot / "config.inc.php"


def bootstrap_pma_files(blowfish_secret: str | None = None) -> str:
    """Renders config.inc.php + the signon script into phpMyAdmin's own
    docroot. Idempotent (safe to re-run); generates a fresh
    blowfish_secret only the first time (config.inc.php's own presence
    signals whether one already exists, matching daemon/dkim.py's
    "reuse if present" convention -- rotating it on every re-run would
    invalidate any live phpMyAdmin session pointlessly)."""
    docroot = Path(settings.pma_docroot)
    config_path = _config_path(docroot)
    if blowfish_secret is None:
        if config_path.exists():
            for line in config_path.read_text().splitlines():
                if "blowfish_secret" in line:
                    # blowfish_secret|tojson renders as a JSON (double-quoted)
                    # string literal, not PHP single-quote syntax.
                    m = re.search(r'"([^"]{32,})"', line)
                    if m:
                        blowfish_secret = m.group(1)
                        break
        if blowfish_secret is None:
            blowfish_secret = secrets.token_urlsafe(32)

    config_template = _env.get_template("pma_config.inc.php.j2")
    config_content = config_template.render(
        blowfish_secret=blowfish_secret,
        mariadb_socket=settings.mariadb_socket,
        token_ttl_seconds=settings.pma_token_ttl_seconds,
    )
    config_path.write_text(config_content)
    config_path.chmod(0o640)

    signon_template = _env.get_template("pma_signon.php.j2")
    signon_content = signon_template.render(pma_token_dir=str(_validate_token_dir_path()))
    signon_path = docroot / "boron_signon.php"
    signon_path.write_text(signon_content)
    signon_path.chmod(0o644)

    try:
        gid = grp.getgrnam("www-data").gr_gid
        os.chown(config_path, 0, gid)
    except KeyError:
        pass

    return blowfish_secret



def validate_service_hostname(hostname: str) -> str:
    hostname = validate_domain(hostname)
    if hostname in (settings.panel_hostname, settings.webmail_hostname):
        raise ValidationError('phpMyAdmin hostname conflicts with another panel service')
    with write_session() as session:
        if session.scalar(select(Domain.id).where(Domain.domain == hostname)):
            raise ValidationError('phpMyAdmin hostname belongs to a customer site')
    return hostname


def _prepare_service_root():
    from daemon.procutil import run
    validate_service_hostname(settings.pma_hostname)
    root = Path(settings.pma_docroot)
    if not root.is_absolute() or root.resolve() != root or not (root/'index.php').is_file():
        raise PmaError('Install phpMyAdmin at its configured absolute document root first')
    try:
        owner = pwd.getpwnam('boron-pma')
    except KeyError:
        run(['useradd', '--system', '--user-group', '--no-create-home',
             '--home-dir', '/nonexistent', '--shell', '/usr/sbin/nologin', 'boron-pma'], check=True)
        owner = pwd.getpwnam('boron-pma')
    if owner.pw_uid < 11 or owner.pw_gid < 10 or owner.pw_shell != '/usr/sbin/nologin':
        raise PmaError('The boron-pma identity must be an unprivileged non-login account')
    # OLS validates docroot UID even though PHP runs explicitly as www-data.
    # The PHP worker must not own the package directory or replace its code.
    os.chown(root, owner.pw_uid, owner.pw_gid)
    challenge = root/'.well-known/acme-challenge'
    if challenge.resolve() != challenge:
        raise PmaError('phpMyAdmin challenge path contains a symbolic link')
    challenge.mkdir(parents=True, exist_ok=True)
    for directory in (root/'.well-known', challenge):
        os.chown(directory, 0, 0)
        directory.chmod(0o755)
    root.chmod(0o555)


def bootstrap_pma() -> None:
    """Full one-time (idempotent) setup: renders config.inc.php + the
    signon script, then provisions the OLS vhost. Run once, explicitly,
    after phpMyAdmin itself is `apt install`ed -- same category as
    bootstrap_webmail (Phase 2 feature 3), not automatic on daemon
    startup (a config-mutating action on startup would be surprising)."""
    from daemon import ols

    _prepare_service_root()
    bootstrap_pma_files()
    ols.bootstrap_pma()
    _record_provision("published", hostname=settings.pma_hostname)


# The Debian/Ubuntu package is what the vhost template, include_path override
# and vendor_config.php lookup above are written for. Without recommends it
# reuses the base php-cli/php-mysql packages and pulls in no web server.
PMA_PACKAGE = "phpmyadmin"
PMA_DEBCONF_SELECTIONS = (
    "phpmyadmin phpmyadmin/dbconfig-install boolean false\n"
    "phpmyadmin phpmyadmin/reconfigure-webserver multiselect\n"
)


INSTALL_BUDGET_SECONDS = 2400
# The update finalizer bounds the whole runtime migration, so an upgrade
# gives phpMyAdmin only a share of it; Server Setup can retry without limit.
UPGRADE_INSTALL_BUDGET_SECONDS = 300
PROVISION_STATE_FILE = "phpmyadmin-provision.json"
_SOURCE_LABELS = {"installer": "during installation", "upgrade": "during a panel update",
                  "setup": "from Server Setup"}


def package_installed() -> bool:
    return (Path(settings.pma_docroot) / "index.php").is_file()


def vhost_config_exists() -> bool:
    """Whether bootstrap_pma has written phpMyAdmin's OLS vhost config. Until
    then any render of the main config that names the vhost fails the OLS
    config test, so a first publication must run before such a render."""
    from daemon import ols
    return Path(ols._pma_vhost_conf_path()).is_file()


def database_component_enabled() -> bool:
    """phpMyAdmin belongs to servers with the database component and MariaDB."""
    import shutil
    from shared.install_profiles import component_enabled
    components = settings.enabled_components or ()
    enabled = ("database" in components) if components else component_enabled(settings.install_profile, "database")
    return enabled and (shutil.which("mariadb") is not None or Path("/usr/sbin/mariadbd").exists())


def install_package(*, budget_seconds: float = INSTALL_BUDGET_SECONDS) -> bool:
    """Install the distribution phpMyAdmin package (idempotent). Returns
    whether anything was installed. Never configures a web server or a
    phpMyAdmin control database; bootstrap_pma owns the configuration.
    Every package-manager step shares one time budget."""
    from daemon.procutil import run
    if package_installed():
        return False
    deadline = time.monotonic() + budget_seconds

    def remaining(limit: float) -> float:
        left = deadline - time.monotonic()
        if left < 1:
            raise PmaError("the phpMyAdmin installation ran out of time")
        return min(limit, left)

    environment = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    lock_wait = f"DPkg::Lock::Timeout={int(min(300, budget_seconds / 3))}"
    run(["debconf-set-selections"], input_text=PMA_DEBCONF_SELECTIONS, check=True, env=environment)
    install = ["apt-get", "install", "-y", "--no-install-recommends", "-o", lock_wait, PMA_PACKAGE]
    if not run(install, timeout=remaining(900), env=environment).ok:
        # Upgraded servers may carry package lists older than the mirror's
        # current dependency versions (404 on fetch); refresh once and retry.
        run(["apt-get", "-o", lock_wait, "update"], timeout=remaining(600), env=environment)
        run(install, check=True, timeout=remaining(900), env=environment)
    if not package_installed():
        raise PmaError(f"phpMyAdmin package did not install its document root at {settings.pma_docroot}")
    return True


def _provision_state_path() -> Path:
    return Path(settings.db_path).parent / PROVISION_STATE_FILE


def _record_provision(status: str, **details) -> None:
    """Keep the last provisioning outcome for readiness checks and the
    administrator status route; best-effort, it never fails the caller."""
    path = _provision_state_path()
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps({"status": status, "at": utcnow().isoformat(),
                                         "pid": os.getpid(), **details}))
        temporary.chmod(0o600)
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        logger.warning("could not record the phpMyAdmin provisioning state at %s", path)


def provision_state() -> dict:
    try:
        state = json.loads(_provision_state_path().read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def failure_summary(exc: BaseException) -> str:
    """One short line for administrators: apt's own "E:" line when there is one."""
    import subprocess
    if isinstance(exc, subprocess.TimeoutExpired):
        return "the package manager did not finish in time"
    lines = [line.strip() for line in str(exc).splitlines() if line.strip()]
    errors = [line[line.index("E: "):] for line in lines if "E: " in line]
    return (errors[-1] if errors else lines[0] if lines else type(exc).__name__)[:300]


def _publish(hostname: str) -> None:
    if settings.pma_hostname == hostname:
        bootstrap_pma()
        return
    # Saving the hostname publishes it (panel_config's reload runs
    # bootstrap_pma) and restores the previous configuration on failure.
    from daemon import panel_config
    panel_config.apply_setup_values({"pma_hostname": hostname}, reload_services=True)


def provision(hostname: str | None = None, *, source: str = "installer",
              budget_seconds: float = INSTALL_BUDGET_SECONDS) -> dict:
    """Install phpMyAdmin, then publish ``hostname`` (saving it to the panel
    configuration) or else the configured hostname, if any. The installer
    and upgrades call this; the outcome is recorded for readiness checks
    and a failure is raised to the caller."""
    target = validate_domain(hostname) if hostname else (settings.pma_hostname or None)
    stage = "install"
    try:
        installed = install_package(budget_seconds=budget_seconds)
        if target:
            stage = "publish"
            _publish(target)
    except Exception as exc:
        summary = failure_summary(exc)
        logger.warning("phpMyAdmin %s failed %s: %s", stage, _SOURCE_LABELS.get(source, source), summary)
        _record_provision("failed", stage=stage, source=source, hostname=target, error=summary)
        raise
    _record_provision("published" if target else "installed", source=source, hostname=target)
    return {"installed": installed, "published": bool(target), "hostname": target}


# Server Setup saves a phpMyAdmin hostname inside a configuration transaction
# that must not wait minutes for apt. panel_config hands such a hostname here
# instead: one background worker installs the package, then publishes the
# most recently requested hostname. The lock is held while it publishes, so
# a hostname saved meanwhile (withdraw_publication) is applied after it.
_publication_lock = threading.RLock()
_pending_hostname: str | None = None
_worker_active = False


def request_publication(hostname: str) -> dict:
    global _pending_hostname, _worker_active
    hostname = validate_domain(hostname)
    with _publication_lock:
        _pending_hostname = hostname
        _record_provision("installing", source="setup", hostname=hostname)
        if not _worker_active:
            _worker_active = True
            threading.Thread(target=_provision_in_background, name="phpmyadmin-provision", daemon=True).start()
    return {"status": "installing", "hostname": hostname}


def withdraw_publication() -> None:
    """Drop a pending background publication: another phpMyAdmin hostname
    (or none) is being saved. Waits for a publication already running."""
    global _pending_hostname
    with _publication_lock:
        _pending_hostname = None
        if not _worker_active:
            # The hostname being saved supersedes an earlier attempt's outcome.
            try:
                _provision_state_path().unlink(missing_ok=True)
            except OSError:
                logger.warning("could not clear the phpMyAdmin provisioning state")


def _provision_in_background() -> None:
    global _pending_hostname, _worker_active
    stage, error = "install", None
    try:
        install_package()
    except Exception as exc:  # noqa: BLE001 - recorded for readiness and the status route
        error = exc
    with _publication_lock:
        # Leave the worker only while holding the lock: a request arriving
        # after this must start a new worker, not wait for this one.
        try:
            hostname, _pending_hostname = _pending_hostname, None
            if error is None and hostname:
                stage = "publish"
                try:
                    _publish(hostname)
                except Exception as exc:  # noqa: BLE001
                    error = exc
            if error is None:
                _record_provision("published" if hostname else "installed", source="setup", hostname=hostname)
            else:
                summary = failure_summary(error)
                logger.warning("phpMyAdmin %s failed from Server Setup: %s", stage, summary)
                _record_provision("failed", stage=stage, source="setup", hostname=hostname, error=summary)
        finally:
            _worker_active = False


def provisioning_status(_params: dict | None = None) -> dict:
    """Installation and publication state, for administrators."""
    state = provision_state()
    installing = state.get("status") == "installing"
    # Only this daemon process runs the background worker; a record it did
    # not finish was cut short by a restart.
    interrupted = installing and not (_worker_active and state.get("pid") == os.getpid())
    failed = state.get("status") == "failed"
    return {
        "installed": package_installed(),
        "published": is_available(),
        "hostname": settings.pma_hostname or None,
        # What the running or last attempt installs and publishes; a held
        # back hostname is saved to the configuration only once published.
        "target_hostname": state.get("hostname"),
        "provisioning": "interrupted" if interrupted else state.get("status"),
        "last_error": state.get("error") if failed else None,
        "last_stage": state.get("stage") if failed else None,
        "last_source": state.get("source"),
        "last_attempt_at": state.get("at"),
    }


def readiness_check() -> dict:
    """Diagnostics row (and so an update advisory) for phpMyAdmin."""
    from daemon.diagnostics import _result
    label = "phpMyAdmin"
    retry = "then save the phpMyAdmin hostname in Panel settings (Hostname & SSL) again to retry."
    if not database_component_enabled():
        return _result("phpmyadmin", label, "pass",
                       "The database component is not enabled on this server, so phpMyAdmin is not used.")
    status = provisioning_status()
    host = status["hostname"]
    if status["provisioning"] == "installing":
        return _result("phpmyadmin", label, "warn",
                       f"phpMyAdmin is being installed for {status['target_hostname']}; it is published when "
                       "the installation finishes.", "Wait a few minutes, then run the checks again.")
    if status["provisioning"] == "interrupted":
        return _result("phpmyadmin", label, "warn", "The phpMyAdmin installation was interrupted by a panel restart.",
                       "If the package manager reports an interrupted run, run 'dpkg --configure -a' on the "
                       "server first; " + retry)
    if status["provisioning"] == "failed":
        action = "installed" if status["last_stage"] == "install" else "published"
        when = _SOURCE_LABELS.get(status["last_source"])
        failure = f"could not be {action}{' ' + when if when else ''}: {status['last_error']}"
        explanation = (f"phpMyAdmin is published at https://{host}, but a later change failed: it {failure}"
                       if status["published"] else f"phpMyAdmin {failure}")
        fix = ("Make sure 'apt-get update' and 'apt-get install phpmyadmin' work on the server, "
               if action == "installed" else "Fix the reported problem, ")
        return _result("phpmyadmin", label, "warn", explanation, fix + retry)
    if status["published"]:
        return _result("phpmyadmin", label, "pass", f"phpMyAdmin is installed and published at https://{host}.")
    if not status["installed"]:
        return _result("phpmyadmin", label, "warn", "phpMyAdmin is not installed.",
                       "Choose a phpMyAdmin hostname in Panel settings (Hostname & SSL); the panel installs and "
                       "publishes it.")
    if not host:
        # Publishing it is the administrator's choice. Every update installs
        # the package on database servers and the finalizer stores warnings
        # as advisories, so a warning here would follow every update.
        return _result("phpmyadmin", label, "pass",
                       "phpMyAdmin is installed but not published: no phpMyAdmin hostname is configured.",
                       "To publish it, choose a phpMyAdmin hostname in Panel settings (Hostname & SSL).")
    return _result("phpmyadmin", label, "warn",
                   f"phpMyAdmin is installed but not published at https://{host}: its sign-on script is missing.",
                   "Save the phpMyAdmin hostname in Panel settings (Hostname & SSL) again to republish it.")
