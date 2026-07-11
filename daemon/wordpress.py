"""One-click WordPress installer (Phase 3 feature 2).

Deliberately does not depend on WP-CLI: this build's own sandbox denied
downloading+executing a third-party CLI tool (wp-cli.phar) from an
agent-chosen source (raw.githubusercontent.com) as a real-time
permission decision -- same category as Phase 2's setuid-helper-binary
and MariaDB-privilege-escalation denials, and handled the same way:
redesigned around the constraint rather than worked around, documented
here and in CHECKPOINT-phase3-2.md.

Instead, this module calls WordPress's own official APIs/mechanisms
directly:
  - `https://api.wordpress.org/core/version-check/1.7/` for the current
    version + download URL (the "official API" the goal names).
  - `https://downloads.wordpress.org/release/...` for the actual core zip.
  - `https://api.wordpress.org/secret-key/1.1/salt/` for real unique
    per-install auth salts (what `wp config create` itself fetches).
  - WordPress core's own `wp_install()` function (`wp-admin/includes/
    upgrade.php`) for the actual "silent install" step -- this is the
    exact function WP-CLI's `wp core install` wraps internally, invoked
    here via a small static PHP bootstrap script run through the
    system's own `php` CLI (already installed, `php8.3-cli`), never
    through wp-cli. Not a fragile HTML-form-scrape of `install.php`.

The install itself always runs as the hosting account's own Linux user
(`runuser -u <username>`, matching every other per-account file
operation in this project -- ARCHITECTURE.md SS6's suEXEC-equivalent
isolation), never as root.
"""
from __future__ import annotations

import json
import logging
import os
import pwd
import secrets
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain, WordPressInstall, WordPressJob
from shared.validation import generate_strong_password, validate_domain, validate_password_strength, validate_username

from daemon import handlers_database
from daemon.procutil import run

logger = logging.getLogger("borond.wordpress")

_executor = ThreadPoolExecutor(max_workers=settings.wp_install_concurrency, thread_name_prefix="wp-install")

# Static, checked into git (daemon/php_helpers/wp_install_helper.php),
# deployed as a plain file under /opt/boron -- deliberately NOT
# written into settings.wp_staging_dir (a subdirectory of
# /var/lib/boron, which shared/db.py locks to root:boron-api
# 0750 for the control-plane DB's sake). This script runs via
# `runuser -u <account>`, so it must be readable by an arbitrary hosting
# account's own uid -- confirmed live: an earlier version of this module
# wrote the helper under wp_staging_dir and every install failed with
# "Could not open input file", since no hosting account uid can even
# traverse into /var/lib/boron, let alone read a file under it.
# /opt/boron itself is already world-traversable/readable (that's
# how the separate, also-unprivileged boron-api user reads this same
# tree to run the app at all), so a plain deployed file here needs no
# special permissioning.
INSTALL_HELPER_PATH = Path(__file__).resolve().parent / "php_helpers" / "wp_install_helper.php"


class WordPressError(Exception):
    pass


def _generate_password(length: int = 20) -> str:
    return generate_strong_password(length)


def fetch_latest_version_and_url() -> tuple[str, str]:
    resp = httpx.get(settings.wp_version_check_url, timeout=20.0, params={"locale": "en_US"})
    resp.raise_for_status()
    data = resp.json()
    offers = data.get("offers") or []
    if not offers:
        raise WordPressError("WordPress.org version-check API returned no offers")
    offer = offers[0]
    return offer["current"], offer["download"]


def _fetch_salts() -> str:
    resp = httpx.get(settings.wp_salt_api_url, timeout=20.0)
    resp.raise_for_status()
    return resp.text


def _download_zip(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes():
                f.write(chunk)


def _safe_extract_target(docroot: str, relative: str) -> str:
    """Zip Slip defense: a malicious/compromised archive can name a member
    `../../../etc/cron.d/evil` -- os.path.join alone happily builds a path
    outside docroot from that. Extraction runs as root (before the caller
    chowns the result to the account's uid), so an unchecked escape here
    is a root-level arbitrary file write -- the same os.path.realpath +
    startswith jail check daemon/filemanager.py uses, applied per member."""
    docroot_real = os.path.realpath(docroot)
    target = os.path.realpath(os.path.join(docroot_real, relative))
    if target != docroot_real and not target.startswith(docroot_real + os.sep):
        raise WordPressError(f"archive member '{relative}' would extract outside the docroot -- refusing (possible zip slip)")
    return target


def _extract_wordpress(zip_path: Path, docroot: str) -> None:
    """The official zip wraps everything in a top-level `wordpress/`
    directory -- strip it so the docroot itself becomes the WP root."""
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            if not name.startswith("wordpress/"):
                continue
            relative = name[len("wordpress/"):]
            if not relative:
                continue
            target = _safe_extract_target(docroot, relative)
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


def _docroot_is_empty_enough(docroot: str) -> bool:
    """Refuses to install over existing customer content -- conservative
    by design (goal gives no "force overwrite" instruction, and silently
    clobbering a docroot is exactly the kind of destructive action this
    project's rules say to avoid without explicit confirmation). Hidden
    entries (e.g. `.well-known`, created by `ensure_docroot` for every
    domain) are Boron's own infra, not customer content, and are
    allowed to remain."""
    if not os.path.isdir(docroot):
        return True
    visible = [name for name in os.listdir(docroot) if not name.startswith(".")]
    return not visible


def _php_str(value: str) -> str:
    """Single-quoted PHP string literal -- unlike a JSON/double-quoted
    string, this is immune to PHP's own `$var`/`{$var}` interpolation,
    which a generated DB password can trigger if it happens to contain a
    literal `$` (confirmed live, Phase 6b Step 1: `json.dumps()`'s output
    dropped into a PHP double-quoted string corrupted DB_PASSWORD with an
    `Undefined variable $LU` warning, breaking every fresh WordPress
    install whose random password happened to contain `$`). Matches the
    same pattern already used in daemon/appinstaller.py's Joomla config
    writer -- duplicated here rather than imported to avoid a circular
    import (appinstaller imports wordpress.install)."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _write_wp_config(docroot: str, db_name: str, db_user: str, db_password: str) -> None:
    salts = _fetch_salts()
    content = (
        "<?php\n"
        f"define('DB_NAME', {_php_str(db_name)});\n"
        f"define('DB_USER', {_php_str(db_user)});\n"
        f"define('DB_PASSWORD', {_php_str(db_password)});\n"
        f"define('DB_HOST', {_php_str('localhost:' + settings.mariadb_socket)});\n"
        "define('DB_CHARSET', 'utf8mb4');\n"
        "define('DB_COLLATE', '');\n"
        "$table_prefix = 'wp_';\n\n"
        f"{salts}\n\n"
        "define('WP_DEBUG', false);\n"
        "define('FS_METHOD', 'direct');\n\n"
        "if ( ! defined('ABSPATH') ) {\n"
        "    define('ABSPATH', __DIR__ . '/');\n"
        "}\n"
        "require_once ABSPATH . 'wp-settings.php';\n"
    )
    path = os.path.join(docroot, "wp-config.php")
    with open(path, "w") as f:
        f.write(content)
    os.chmod(path, 0o640)


def _run_silent_install(
    docroot: str, username: str, home_dir: str, site_url: str, title: str, admin_user: str, admin_email: str, admin_password: str
) -> dict:
    result = run(
        [
            "runuser", "-u", username, "--",
            "env", f"HOME={home_dir}",
            settings.php_cli_bin, str(INSTALL_HELPER_PATH),
            docroot, site_url, title, admin_user, admin_email, admin_password,
        ],
        timeout=90,
    )
    if not result.ok:
        raise WordPressError(f"WordPress install script failed: {result.stderr.strip() or result.stdout.strip()}")
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise WordPressError(f"WordPress install script returned unparseable output: {result.stdout!r}") from exc
    if not payload.get("success"):
        raise WordPressError(f"WordPress install did not report success: {payload}")
    return payload


def _account_and_domain(username: str, domain_name: str) -> tuple[int, str]:
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        domain_row = session.scalar(
            select(Domain).where(Domain.domain == domain_name, Domain.account_id == account.id)
        )
        if domain_row is None:
            raise RuntimeError(f"domain '{domain_name}' not found for account '{username}'")
        return account.id, domain_row.docroot


def _allocate_database(username: str) -> tuple[dict, str]:
    """Tries `wp` first (the common case: one WP install per account), and
    a random suffix on collision (an account installing WordPress on a
    second domain) -- returns (grant_dict, suffix_used)."""
    last_error: Exception | None = None
    for attempt in range(5):
        suffix = "wp" if attempt == 0 else f"wp{secrets.token_hex(2)}"
        try:
            return handlers_database.create_database({"username": username, "name": suffix}), suffix
        except RuntimeError as exc:
            last_error = exc
            continue
    raise WordPressError(f"could not allocate a database for the WordPress install: {last_error}")


def install(params: dict) -> dict:
    """Synchronous core install -- see trigger_install for the async job
    wrapper the API/UI actually calls."""
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])
    title = (params.get("title") or domain_name).strip()
    admin_user = (params.get("admin_user") or "admin").strip()
    admin_email = (params.get("admin_email") or f"webmaster@{domain_name}").strip()
    admin_password = validate_password_strength(params["admin_password"]) if params.get("admin_password") else _generate_password()

    account_id, docroot = _account_and_domain(username, domain_name)

    with write_session() as session:
        existing = session.scalar(select(WordPressInstall).where(WordPressInstall.domain == domain_name))
        if existing is not None:
            raise WordPressError(f"WordPress is already installed for '{domain_name}' -- remove it first to reinstall")

    if os.path.exists(os.path.join(docroot, "wp-config.php")):
        raise WordPressError(f"'{docroot}' already has a wp-config.php -- refusing to overwrite an existing install")
    if not _docroot_is_empty_enough(docroot):
        raise WordPressError(
            f"'{docroot}' is not empty -- a WordPress install requires an empty docroot "
            "(move or back up existing content first)"
        )

    pw = pwd.getpwnam(username)
    home_dir = f"{settings.home_base}/{username}"

    version, download_url = fetch_latest_version_and_url()
    db_grant, suffix = _allocate_database(username)

    try:
        staging_zip = Path(settings.wp_staging_dir) / f"wordpress-{version}-{secrets.token_hex(4)}.zip"
        try:
            _download_zip(download_url, staging_zip)
            _extract_wordpress(staging_zip, docroot)
        finally:
            staging_zip.unlink(missing_ok=True)

        _write_wp_config(docroot, db_grant["db_name"], db_grant["db_user"], db_grant["password"])
        run(["chown", "-R", f"{pw.pw_uid}:{pw.pw_gid}", docroot], check=True)

        site_url = f"https://{domain_name}"
        _run_silent_install(docroot, username, home_dir, site_url, title, admin_user, admin_email, admin_password)
    except Exception:
        try:
            handlers_database.drop_database({"username": username, "name": suffix})
        except Exception:
            logger.exception("failed to clean up database after a failed WordPress install for '%s'", domain_name)
        raise

    run(["chown", "-R", f"{pw.pw_uid}:{pw.pw_gid}", docroot], check=True)

    with write_session() as session:
        session.add(
            WordPressInstall(
                account_id=account_id,
                domain=domain_name,
                db_name=db_grant["db_name"],
                db_user=db_grant["db_user"],
                wp_version=version,
                admin_user=admin_user,
            )
        )

    return {
        "domain": domain_name,
        "admin_url": f"{site_url}/wp-admin/",
        "admin_user": admin_user,
        "admin_password": admin_password,
        "db_name": db_grant["db_name"],
        "wp_version": version,
        "status": "installed",
    }


def _job_to_dict(job: WordPressJob, *, reveal_password: bool) -> dict:
    return {
        "id": job.id,
        "account_id": job.account_id,
        "domain": job.domain,
        "status": job.status,
        "progress_message": job.progress_message,
        "error": job.error,
        "admin_url": job.admin_url,
        "admin_user": job.admin_user,
        "admin_password": job.admin_password if reveal_password else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _update_job(job_id: int, **fields) -> None:
    with write_session() as session:
        job = session.get(WordPressJob, job_id)
        if job is None:
            return
        for key, value in fields.items():
            setattr(job, key, value)


def trigger_install(params: dict) -> dict:
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        job = WordPressJob(account_id=account.id, domain=domain_name, status="pending", progress_message="queued")
        session.add(job)
        session.flush()
        job_id = job.id
        result = _job_to_dict(job, reveal_password=False)

    _executor.submit(_run_install_job, job_id, params)
    return result


def _run_install_job(job_id: int, params: dict) -> None:
    from shared.models import utcnow

    _update_job(job_id, status="running", progress_message="downloading WordPress core")
    try:
        _update_job(job_id, progress_message="creating database")
        result = install(params)
    except Exception as exc:  # noqa: BLE001 - report to the job row, don't crash the worker thread
        logger.exception("WordPress install job %d failed", job_id)
        _update_job(job_id, status="failed", error=str(exc), progress_message="failed", completed_at=utcnow())
        return

    _update_job(
        job_id,
        status="completed",
        progress_message="done",
        admin_url=result["admin_url"],
        admin_user=result["admin_user"],
        admin_password=result["admin_password"],
        completed_at=utcnow(),
    )


def get_job(params: dict) -> dict:
    """job_id alone is not a capability -- it's a small sequential integer,
    guessable/enumerable by any authenticated customer. Without an
    ownership check here, any account could poll another account's job and
    read its one-time-reveal admin_password (see WordPressJob's docstring),
    also permanently burning the reveal before the real owner ever sees
    it -- a real cross-account credential-theft-plus-denial-of-view bug,
    found auditing this module for the same class of missing
    domain/account ownership check found across several other routers
    this phase (docs/CHECKPOINT-phase4-0b-cross-account-idor.md). username is now
    required and cross-checked against the job's own account_id, mirroring
    install()'s own _account_and_domain check; a mismatch is reported
    identically to a nonexistent job_id, so this endpoint can't be used to
    enumerate which job IDs belong to other accounts either."""
    job_id = int(params["job_id"])
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        job = session.get(WordPressJob, job_id)
        if job is None or job.account_id != account.id:
            raise WordPressError(f"WordPress install job {job_id} not found")
        reveal = job.status == "completed" and job.admin_password is not None
        result = _job_to_dict(job, reveal_password=reveal)
        # One-time reveal: the first successful read clears the persisted
        # password so it can never leak from this row again (a second
        # poll, an admin browsing job history later, a DB dump/backup
        # taken after this point) -- see WordPressJob's docstring.
        if reveal:
            job.admin_password = None
    return result


def list_installs(params: dict) -> dict:
    username = params.get("username")
    with write_session() as session:
        query = select(WordPressInstall)
        if username:
            account = session.scalar(select(Account).where(Account.username == username))
            if account is None:
                raise RuntimeError(f"account '{username}' not found")
            query = query.where(WordPressInstall.account_id == account.id)
        rows = session.scalars(query).all()
        return {
            "installs": [
                {
                    "id": r.id,
                    "domain": r.domain,
                    "db_name": r.db_name,
                    "db_user": r.db_user,
                    "wp_version": r.wp_version,
                    "admin_user": r.admin_user,
                    "installed_at": r.installed_at.isoformat() if r.installed_at else None,
                }
                for r in rows
            ]
        }
