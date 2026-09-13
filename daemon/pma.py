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
import time
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, DatabaseGrant, Domain, PmaToken, utcnow
from shared.validation import validate_username, validate_domain, ValidationError

from daemon import mariadb
from daemon.handlers_database import _resolve_existing_db_name

logger = logging.getLogger("borond.pma")

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)


class PmaError(Exception):
    pass


def _token_dir() -> Path:
    """mkdir(mode=0o770) alone sets permission *bits*, not group
    *ownership* -- a directory borond (root) creates defaults to
    group "root", which www-data isn't a member of, making the mode bits
    irrelevant. Confirmed live: token files were written correctly but
    every signon attempt still 403'd with "token not found", because
    www-data could not even traverse into the directory to read them.
    Explicit chown here, every call (idempotent, cheap), same pattern
    server.py's amain() already uses for /run/boron's socket dir."""
    path = Path(settings.pma_token_dir)
    path.mkdir(parents=True, exist_ok=True, mode=0o770)
    try:
        gid = grp.getgrnam("www-data").gr_gid
        os.chown(path, 0, gid)
        os.chmod(path, 0o770)
    except KeyError:
        logger.warning("www-data group not found; pma token dir left root-only, signon will fail")
    return path


def create_token(params: dict) -> dict:
    username = validate_username(params["username"])
    db_name = _resolve_existing_db_name(username, params["name"])

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise PmaError(f"account '{username}' not found")
        grant = session.scalar(
            select(DatabaseGrant).where(DatabaseGrant.account_id == account.id, DatabaseGrant.db_name == db_name)
        )
        if grant is None:
            raise PmaError(f"database '{db_name}' not found for account '{username}'")
        account_id = account.id

    ephemeral_user = f"boronphpmyadminlogin_{secrets.token_hex(6)}"
    ephemeral_password = mariadb.generate_password()
    mariadb.create_db_user(ephemeral_user, ephemeral_password)
    token_file = None
    try:
        mariadb.grant_exact_database(db_name, ephemeral_user)

        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expires_at_epoch = int(time.time()) + settings.pma_token_ttl_seconds

        token_file = _token_dir() / f"{token_hash}.json"
        token_file.write_text(
            json.dumps(
                {
                    "db_user": ephemeral_user,
                    "db_password": ephemeral_password,
                    "db_name": db_name,
                    "expires_at": expires_at_epoch,
                }
            )
        )
        token_file.chmod(0o640)
        try:
            gid = grp.getgrnam("www-data").gr_gid
            os.chown(token_file, 0, gid)
        except KeyError:
            logger.warning("www-data group not found; pma token file left root-only, signon will fail")

        with write_session() as session:
            session.add(
                PmaToken(
                    token_hash=token_hash,
                    account_id=account_id,
                    db_name=db_name,
                    ephemeral_db_user=ephemeral_user,
                    expires_at=utcnow() + dt.timedelta(seconds=settings.pma_token_ttl_seconds),
                )
            )
    except Exception:
        if token_file is not None:
            token_file.unlink(missing_ok=True)
        mariadb.drop_db_user(ephemeral_user)
        raise

    if not settings.pma_hostname:
        pma_url = None
    else:
        pma_url = f"https://{settings.pma_hostname}/boron_signon.php?token={token}"

    return {
        "token": token,
        "db_name": db_name,
        "pma_url": pma_url,
        "expires_in_seconds": settings.pma_token_ttl_seconds,
    }


def cleanup_expired_tokens() -> int:
    """Called periodically (scripts/pma_token_cleanup.py, system cron,
    mirroring usage_snapshot.py/backup_scheduler.py's established
    pattern): drops the ephemeral MariaDB user + any still-present token
    file for every PmaToken row past its expiry, whether or not it was
    ever actually redeemed (a redeemed token's file is already gone --
    signon.php deletes it on first use -- but the ephemeral MariaDB user
    still needs dropping either way, since nothing else ever does that).
    Returns the number of tokens cleaned up."""
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
        token_file = _token_dir() / f"{token_hash}.json"
        token_file.unlink(missing_ok=True)
        with write_session() as session:
            row = session.get(PmaToken, token_id)
            if row is not None:
                session.delete(row)
        cleaned += 1

    return cleaned


def bootstrap_pma_files(blowfish_secret: str | None = None) -> str:
    """Renders config.inc.php + the signon script into phpMyAdmin's own
    docroot. Idempotent (safe to re-run); generates a fresh
    blowfish_secret only the first time (config.inc.php's own presence
    signals whether one already exists, matching daemon/dkim.py's
    "reuse if present" convention -- rotating it on every re-run would
    invalidate any live phpMyAdmin session pointlessly)."""
    docroot = Path(settings.pma_docroot)
    config_path = docroot / "config.inc.php"
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
    signon_content = signon_template.render(pma_token_dir=settings.pma_token_dir)
    signon_path = docroot / "boron_signon.php"
    signon_path.write_text(signon_content)
    signon_path.chmod(0o644)

    try:
        gid = grp.getgrnam("www-data").gr_gid
        os.chown(config_path, 0, gid)
    except KeyError:
        pass

    return blowfish_secret



def _prepare_service_root():
    from daemon.procutil import run
    hostname = validate_domain(settings.pma_hostname)
    if hostname in (settings.panel_hostname, settings.webmail_hostname):
        raise ValidationError('phpMyAdmin hostname conflicts with another panel service')
    with write_session() as session:
        if session.scalar(select(Domain.id).where(Domain.domain == hostname)):
            raise ValidationError('phpMyAdmin hostname belongs to a customer site')
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
    root.chmod(0o555)
    challenge = root/'.well-known/acme-challenge'
    if challenge.resolve() != challenge:
        raise PmaError('phpMyAdmin challenge path contains a symbolic link')
    challenge.mkdir(parents=True, exist_ok=True)
    for directory in (root/'.well-known', challenge):
        os.chown(directory, 0, 0)
        directory.chmod(0o755)


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
