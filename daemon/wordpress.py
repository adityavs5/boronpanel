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
import re
import secrets
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain, WordPressInstall, WordPressJob, WordPressSiteState, utcnow
from shared.validation import generate_strong_password, validate_domain, validate_password_strength, validate_username

from daemon import handlers_database
from daemon.procutil import run
from daemon.safeio import secure_mkdirs, secure_write_file_beneath

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


MAX_WORDPRESS_DOWNLOAD_BYTES = 256 * 1024 * 1024
MAX_WORDPRESS_EXPANDED_BYTES = 1024 * 1024 * 1024
MAX_WORDPRESS_FILE_BYTES = 128 * 1024 * 1024
MAX_WORDPRESS_ARCHIVE_MEMBERS = 100_000


def _download_zip(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as resp:
        resp.raise_for_status()
        if int(resp.headers.get("content-length", "0") or 0) > MAX_WORDPRESS_DOWNLOAD_BYTES:
            raise WordPressError("WordPress download exceeds the size limit")
        written = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes():
                written += len(chunk)
                if written > MAX_WORDPRESS_DOWNLOAD_BYTES:
                    raise WordPressError("WordPress download exceeds the size limit")
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
        members = zf.infolist()
        if len(members) > MAX_WORDPRESS_ARCHIVE_MEMBERS:
            raise WordPressError("WordPress archive contains too many members")
        expanded = 0
        seen: set[str] = set()
        # Validate the complete index before writing any part of the site.
        for info in members:
            name = info.filename
            if not name.startswith("wordpress/"):
                continue
            relative = name[len("wordpress/"):]
            if not relative:
                continue
            relative = relative.replace("\\", "/")
            if len(relative.encode("utf-8", errors="replace")) > 2048 or len(Path(relative).parts) > 64:
                raise WordPressError("WordPress archive contains an excessively long or deep path")
            if relative in seen:
                raise WordPressError("WordPress archive contains a duplicate path")
            seen.add(relative)
            expanded += info.file_size
            if expanded > MAX_WORDPRESS_EXPANDED_BYTES or info.file_size > MAX_WORDPRESS_FILE_BYTES:
                raise WordPressError("WordPress archive exceeds the extraction limit")
            _safe_extract_target(docroot, relative)
        root_stat = os.stat(docroot, follow_symlinks=False)
        for info in members:
            name = info.filename
            if not name.startswith("wordpress/"):
                continue
            relative = name[len("wordpress/"):]
            if not relative:
                continue
            relative = relative.replace("\\", "/")
            if info.is_dir():
                secure_mkdirs(docroot, relative.rstrip("/"), root_stat.st_uid, root_stat.st_gid, 0o750)
                continue
            with zf.open(info) as src:
                data = src.read(MAX_WORDPRESS_FILE_BYTES + 1)
                if len(data) > MAX_WORDPRESS_FILE_BYTES:
                    raise WordPressError("WordPress archive member exceeds the file limit")
                secure_write_file_beneath(docroot, relative, data, root_stat.st_uid, root_stat.st_gid, 0o640)


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
    visible = [name for name in os.listdir(docroot) if not name.startswith(".") and not (name == "error_pages" and os.path.isdir(os.path.join(docroot, name)) and not os.listdir(os.path.join(docroot, name)))]
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
            docroot, site_url, title, admin_user, admin_email,
        ],
        timeout=90,
        input_text=admin_password,
        redact=[admin_password],
    )
    if not result.ok:
        # WordPress/plugins can put sensitive install values into diagnostics.
        # These errors flow into a job row and the daemon log, so never echo
        # raw subprocess output back to the panel.
        raise WordPressError("WordPress install script failed; check site files and retry")
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise WordPressError("WordPress install script returned an invalid result") from exc
    if payload != {"success": True}:
        raise WordPressError("WordPress install script did not report success")
    return {"success": True}


def _account_and_domain(username: str, domain_name: str) -> tuple[int, str]:
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        if account.status != "active":
            raise RuntimeError(f"cannot install WordPress for an account in status '{account.status}'")
        domain_row = session.scalar(
            select(Domain).where(Domain.domain == domain_name, Domain.account_id == account.id)
        )
        if domain_row is None:
            raise RuntimeError(f"domain '{domain_name}' not found for account '{username}'")
        return account.id, domain_row.docroot


def _install_target(docroot: str, path: str) -> str:
    """QA round 2, item 3 (subdirectory installs): resolve the real
    install directory -- the domain's docroot itself if `path` is empty,
    else that subdirectory of it. Same realpath-containment jail as
    `_safe_extract_target` above (a crafted `path` like "../../etc" must
    never escape the docroot). The caller creates a missing subdirectory
    through the symlink-safe directory primitive before extraction."""
    path = (path or "").strip().strip("/")
    if not path:
        return docroot
    docroot_real = os.path.realpath(docroot)
    target = os.path.realpath(os.path.join(docroot_real, path))
    if target != docroot_real and not target.startswith(docroot_real + os.sep):
        raise WordPressError(f"path '{path}' would install outside the domain's docroot -- refusing")
    return target


def _suffix_hint(path: str) -> str:
    """Best-effort short DB-name hint from a subdirectory path (e.g.
    "blog" -> "wp_blog") -- purely cosmetic (helps an admin browsing the
    database list tell which DB belongs to which install at a glance);
    falls back to a random suffix on any collision or unsafe/empty path,
    exactly like the existing root-install retry logic already does, so
    an unusual subdirectory name can never block an install."""
    cleaned = re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")[:20]
    return f"wp_{cleaned}" if cleaned else "wp"


def _allocate_database(username: str, path: str = "") -> tuple[dict, str]:
    """Tries a preferred suffix first (`wp` for a root install, a
    path-derived hint for a subdirectory one), and a random suffix on
    collision (an account installing WordPress a second time, at another
    domain or subdirectory) -- returns (grant_dict, suffix_used)."""
    preferred = _suffix_hint(path) if path else "wp"
    last_error: Exception | None = None
    for attempt in range(5):
        suffix = preferred if attempt == 0 else f"wp{secrets.token_hex(2)}"
        try:
            return handlers_database.create_database({"username": username, "name": suffix}), suffix
        except RuntimeError as exc:
            last_error = exc
            continue
    raise WordPressError(f"could not allocate a database for the WordPress install: {last_error}")


def website_url(domain: str, path: str = '', protocol: str = 'https', use_www: bool = False) -> str:
    domain = validate_domain(domain)
    if protocol not in ('http', 'https') or not isinstance(use_www, bool):
        raise WordPressError('Choose HTTP or HTTPS and a valid www option')
    if use_www:
        if domain.startswith('www.'):
            raise WordPressError('This domain already starts with www; select the address without an extra www prefix')
        with write_session() as session:
            if session.scalar(select(Domain).where(Domain.domain == 'www.' + domain)) is not None:
                raise WordPressError('The www address is configured as a separate site; select that domain instead')
        if 'www.' + domain in (settings.webmail_hostname, settings.pma_hostname):
            raise WordPressError('The www address is reserved for a panel service')
    return protocol + '://' + ('www.' if use_www else '') + domain + ('/' + path.strip('/') if path else '')


def install(params: dict) -> dict:
    """Synchronous core install -- see trigger_install for the async job
    wrapper the API/UI actually calls. QA round 2, item 3: an optional
    `path` (relative to the domain's docroot, e.g. "blog") installs into a
    subdirectory instead of the docroot itself, so a domain can host
    multiple independently-tracked WordPress installs (root + any number
    of subdirectories)."""
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])
    path = (params.get("path") or "").strip().strip("/")
    site_url = website_url(domain_name, path, params.get("protocol", "https"), params.get("use_www", False))
    title = (params.get("title") or domain_name).strip()
    admin_user = (params.get("admin_user") or "admin").strip()
    admin_email = (params.get("admin_email") or f"webmaster@{domain_name}").strip()
    admin_password = validate_password_strength(params["admin_password"]) if params.get("admin_password") else _generate_password()

    account_id, docroot = _account_and_domain(username, domain_name)
    target_dir = _install_target(docroot, path)

    with write_session() as session:
        existing = session.scalar(
            select(WordPressInstall).where(WordPressInstall.domain == domain_name, WordPressInstall.path == path)
        )
        if existing is not None:
            where = f"'{domain_name}'" if not path else f"'{domain_name}/{path}'"
            raise WordPressError(f"WordPress is already installed at {where} -- remove it first to reinstall")

    if os.path.exists(os.path.join(target_dir, "wp-config.php")):
        raise WordPressError(f"'{target_dir}' already has a wp-config.php -- refusing to overwrite an existing install")
    if not _docroot_is_empty_enough(target_dir):
        raise WordPressError(
            f"'{target_dir}' is not empty -- a WordPress install requires an empty directory "
            "(move or back up existing content first)"
        )

    pw = pwd.getpwnam(username)
    home_dir = f"{settings.home_base}/{username}"
    if path:
        # A subdirectory install has no extraction root yet. Create it
        # beneath the already-provisioned docroot through O_NOFOLLOW fds,
        # with the account's ownership and the same 0750 directory mode used
        # for normal account docroots.
        secure_mkdirs(docroot, path, pw.pw_uid, pw.pw_gid, 0o750)

    version, download_url = fetch_latest_version_and_url()
    db_grant, suffix = _allocate_database(username, path)

    try:
        staging_zip = Path(settings.wp_staging_dir) / f"wordpress-{version}-{secrets.token_hex(4)}.zip"
        try:
            _download_zip(download_url, staging_zip)
            _extract_wordpress(staging_zip, target_dir)
        finally:
            staging_zip.unlink(missing_ok=True)

        _write_wp_config(target_dir, db_grant["db_name"], db_grant["db_user"], db_grant["password"])
        run(["chown", "-R", f"{pw.pw_uid}:{pw.pw_gid}", target_dir], check=True)

        _run_silent_install(target_dir, username, home_dir, site_url, title, admin_user, admin_email, admin_password)
    except Exception:
        try:
            handlers_database.drop_database({"username": username, "name": suffix})
        except Exception:
            logger.exception("failed to clean up database after a failed WordPress install for '%s'", domain_name)
        raise

    run(["chown", "-R", f"{pw.pw_uid}:{pw.pw_gid}", target_dir], check=True)
    # Newly created subfolders and extracted modes can mask the default ACL.
    # Restore OLS worker access as the account, without root following site files.
    run(["runuser", "-u", username, "--", "setfacl", "-R", "-m", "u:nobody:rX", "-d", "-m", "u:nobody:rX", target_dir], check=True)

    with write_session() as session:
        state = session.scalar(select(WordPressSiteState).where(
            WordPressSiteState.account_id == account_id,
            WordPressSiteState.domain == domain_name, WordPressSiteState.path == path))
        if state is None:
            state = WordPressSiteState(account_id=account_id, domain=domain_name, path=path)
            session.add(state)
        state.hidden = False
        state.site_url = site_url
        state.scanned_at = utcnow()
        session.add(
            WordPressInstall(
                account_id=account_id,
                domain=domain_name,
                path=path,
                db_name=db_grant["db_name"],
                db_user=db_grant["db_user"],
                wp_version=version,
                admin_user=admin_user,
            )
        )

    return {
        "domain": domain_name,
        "path": path,
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


def _trigger_install(params: dict) -> dict:
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


def trigger_install(params: dict) -> dict:
    from daemon.wpmanager import _operation_lock, ensure_idle
    with _operation_lock:
        ensure_idle(params['username'])
        return _trigger_install(params)


def _run_install_job(job_id: int, params: dict) -> None:
    from shared.models import utcnow

    try:
        with write_session() as session:
            job = session.get(WordPressJob, job_id)
            if job is None or job.status != "pending":
                return
            account = session.get(Account, job.account_id)
            if account is None:
                raise RuntimeError("WordPress install account no longer exists")
            if account.status != "active":
                raise RuntimeError(f"cannot install WordPress for an account in status '{account.status}'")
            domain_row = session.scalar(select(Domain).where(Domain.account_id == account.id, Domain.domain == job.domain))
            if domain_row is None:
                raise RuntimeError("WordPress install domain no longer belongs to this account")
            runtime_params = dict(params)
            runtime_params["username"] = account.username
            runtime_params["domain"] = job.domain
            job.status = "running"
            job.progress_message = "downloading WordPress core"

        _update_job(job_id, progress_message="creating database")
        result = install(runtime_params)
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
                    "path": r.path,
                    "db_name": r.db_name,
                    "db_user": r.db_user,
                    "wp_version": r.wp_version,
                    "admin_user": r.admin_user,
                    "installed_at": r.installed_at.isoformat() if r.installed_at else None,
                }
                for r in rows
            ]
        }
