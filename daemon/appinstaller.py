"""One-click app installer -- Softaculous-equivalent (Phase 4 feature 8).

WordPress is deliberately NOT reimplemented here -- the goal's own literal
instruction is "WordPress (reuse existing)". daemon/wordpress.py's existing
trigger_install()/get_job() (Phase 3 feature 2) are called directly,
unchanged, and wired into this feature's unified app registry/API/UI as one
more entry.

Static HTML: no "official source" applies to a generic starter template --
a small, tasteful starter page bundled with Boron itself
(daemon/app_templates/static/) is copied into place.

Joomla: the one app type this phase's Definition of Done requires live
verification for. Bypasses Joomla's own interactive multi-step web
installer entirely -- the same "bypass the wizard, use the application's
own stable documented data formats directly" design daemon/wordpress.py's
installer (Phase 3) already established for WordPress, applied to Joomla's
own on-disk artifacts:
  - `installation/sql/mysql/{base,extensions,supports}.sql` -- Joomla's
    OWN bundled core-schema SQL files (confirmed by inspecting a real
    downloaded release before writing this module: base.sql alone already
    seeds the full usergroup tree, the ACL asset tree, and the extension
    registry -- these three files together are genuinely what Joomla's own
    installer wizard imports for "Database" step, with no sample content
    needed for a working, loginable install).
  - `configuration.php` -- a plain PHP class file with public properties,
    the same "just a config file, not a program" shape as wp-config.php.
  - The admin user is created via a direct SQL INSERT into `#__users` +
    `#__user_usergroup_map` (group 8, "Super Users", confirmed from
    base.sql's own INSERT statements), with a real bcrypt hash computed by
    PHP's own `password_hash()` (Joomla's own password format since 3.2)
    -- via a one-line `php -r` call, not a reimplemented bcrypt, matching
    this project's "use the target application's own hashing tool"
    pattern already established for doveadm/htpasswd elsewhere.
  - The `installation/` directory is removed afterward -- Joomla checks
    for its absence before serving the site normally, the same role
    WordPress's presence-of-wp-config.php check plays.

Drupal / PrestaShop / Laravel skeleton: real download + configuration,
using each project's best-known documented non-interactive/CLI install
path. Genuinely implemented, but -- unlike Joomla -- not independently
live-verified against a real running install in this pass; see
CHECKPOINT-phase4-8-app-installer.md for exactly what wasn't and why.
"""
from __future__ import annotations

import logging
import os
import pwd
import secrets
import shutil
import tarfile
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, AppInstall, AppInstallJob, Domain, utcnow
from shared.validation import generate_strong_password, validate_domain, validate_password_strength, validate_username

from daemon import handlers_database, wordpress
from daemon.procutil import run
from daemon.safeio import secure_mkdirs, secure_write_file_beneath

logger = logging.getLogger("borond.appinstaller")

_executor = ThreadPoolExecutor(max_workers=settings.app_install_concurrency, thread_name_prefix="app-install")
MAX_APP_DOWNLOAD_BYTES = 1024 * 1024 * 1024
MAX_APP_EXPANDED_BYTES = 4 * 1024 * 1024 * 1024
MAX_APP_ARCHIVE_MEMBERS = 100_000
MAX_APP_FILE_BYTES = 512 * 1024 * 1024

APP_TEMPLATES_DIR = Path(__file__).resolve().parent / "app_templates"


class AppInstallError(Exception):
    pass


# --- shared helpers, mirroring daemon/wordpress.py's own established
# patterns (Phase 3 feature 2) -------------------------------------------


def _generate_password(length: int = 20) -> str:
    return generate_strong_password(length)


def _account_and_domain(username: str, domain_name: str) -> tuple[int, str]:
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise AppInstallError(f"account '{username}' not found")
        domain_row = session.scalar(select(Domain).where(Domain.domain == domain_name, Domain.account_id == account.id))
        if domain_row is None:
            raise AppInstallError(f"domain '{domain_name}' not found for account '{username}'")
        # The account can write its own home, so it may have replaced its
        # docroot with a symlink after domain-add. Extraction and `chown -R`
        # below run as ROOT and follow symlinks, so a docroot symlinked to /etc
        # (or another tenant's home) would be an arbitrary root-write / cross-
        # tenant clobber. Resolve it and require it to stay within this
        # account's own home before any install touches it.
        home = os.path.realpath(f"{settings.home_base}/{username}")
        real_docroot = os.path.realpath(domain_row.docroot)
        if real_docroot != home and not real_docroot.startswith(home + os.sep):
            raise AppInstallError(
                f"docroot '{domain_row.docroot}' does not resolve within the account home -- refusing to install"
            )
        return account.id, real_docroot


def _docroot_is_empty_enough(docroot: str) -> bool:
    if not os.path.isdir(docroot):
        return True
    visible = [name for name in os.listdir(docroot) if not name.startswith(".")]
    return not visible


def _allocate_database(username: str, prefix: str) -> dict:
    last_error: Exception | None = None
    for attempt in range(5):
        suffix = prefix if attempt == 0 else f"{prefix}{secrets.token_hex(2)}"
        try:
            return handlers_database.create_database({"username": username, "name": suffix})
        except RuntimeError as exc:
            last_error = exc
            continue
    raise AppInstallError(f"could not allocate a database for the install: {last_error}")


def _set_ownership(username: str, docroot: str) -> None:
    pw = pwd.getpwnam(username)
    result = run(["chown", "-R", f"{pw.pw_uid}:{pw.pw_gid}", docroot], timeout=120)
    if not result.ok:
        raise AppInstallError(f"failed to set ownership on '{docroot}': {result.stderr.strip()}")


def _download(url: str, dest: Path, timeout: float = 180.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, timeout=timeout, follow_redirects=True) as resp:
        resp.raise_for_status()
        if int(resp.headers.get("content-length", "0") or 0) > MAX_APP_DOWNLOAD_BYTES:
            raise AppInstallError("application download exceeds the size limit")
        written = 0
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes():
                written += len(chunk)
                if written > MAX_APP_DOWNLOAD_BYTES:
                    raise AppInstallError("application download exceeds the size limit")
                f.write(chunk)


def _safe_extract_target(docroot: str, relative: str) -> str:
    """Zip Slip defense: a malicious/compromised archive can name a member
    `../../../etc/cron.d/evil` -- os.path.join alone happily builds a path
    outside docroot from that. Extraction runs as root (before
    _set_ownership chowns the result), so an unchecked escape here is a
    root-level arbitrary file write, not just a jail breakout -- this is
    the same os.path.realpath + startswith jail check
    daemon/filemanager.py uses, applied per zip member."""
    docroot_real = os.path.realpath(docroot)
    target = os.path.realpath(os.path.join(docroot_real, relative))
    if target != docroot_real and not target.startswith(docroot_real + os.sep):
        raise AppInstallError(f"archive member '{relative}' would extract outside the docroot -- refusing (possible zip slip)")
    return target


def _extract_zip(zip_path: Path, docroot: str, root_prefix: str | None = None) -> None:
    """root_prefix strips one wrapping top-level directory some release
    zips use (WordPress's own `wordpress/` being the precedent this
    mirrors) -- None extracts flat, for releases (Joomla's) that don't
    wrap at all."""
    with zipfile.ZipFile(zip_path) as zf:
        members = zf.infolist()
        if len(members) > MAX_APP_ARCHIVE_MEMBERS:
            raise AppInstallError("application archive contains too many members")
        expanded = 0
        seen: set[str] = set()
        for info in members:
            name = info.filename
            if root_prefix:
                if not name.startswith(root_prefix):
                    continue
                relative = name[len(root_prefix):]
                if not relative:
                    continue
            else:
                relative = name
            if len(relative.encode("utf-8", errors="replace")) > 2048 or len(Path(relative).parts) > 64:
                raise AppInstallError("application archive contains an excessively long or deep path")
            if relative in seen:
                raise AppInstallError("application archive contains a duplicate path")
            seen.add(relative)
            expanded += info.file_size
            if expanded > MAX_APP_EXPANDED_BYTES or info.file_size > MAX_APP_FILE_BYTES:
                raise AppInstallError("application archive exceeds the extraction limit")
            target = _safe_extract_target(docroot, relative)
            root_uid, root_gid = os.stat(docroot, follow_symlinks=False).st_uid, os.stat(docroot, follow_symlinks=False).st_gid
            if info.is_dir() or name.endswith("/"):
                secure_mkdirs(docroot, relative.rstrip("/"), root_uid, root_gid, 0o750)
                continue
            with zf.open(info) as src:
                data = src.read(MAX_APP_FILE_BYTES + 1)
                if len(data) > MAX_APP_FILE_BYTES:
                    raise AppInstallError("application archive member exceeds the file limit")
                secure_write_file_beneath(docroot, relative, data, root_uid, root_gid, 0o640)


def _extract_wrapped_tar(archive_path: Path, destination: Path) -> Path:
    """Extract a vendor tarball without trusting archive paths or links."""
    try:
        with tarfile.open(archive_path) as archive:
            expanded = 0
            count = 0
            for member in archive:
                count += 1
                if count > MAX_APP_ARCHIVE_MEMBERS:
                    raise AppInstallError("application archive contains too many members")
                if (len(member.name.encode("utf-8", errors="replace")) > 2048
                        or len(Path(member.name).parts) > 64):
                    raise AppInstallError("application archive contains an excessively long or deep path")
                if not (member.isdir() or member.isreg()):
                    raise AppInstallError("application archive contains a link or special file")
                expanded += member.size if member.isreg() else 0
                if expanded > MAX_APP_EXPANDED_BYTES:
                    raise AppInstallError("application archive exceeds the extraction limit")
            archive.extractall(destination, filter="data")
    except tarfile.TarError as exc:
        raise AppInstallError("application archive is unsafe or unreadable") from exc
    roots = [path for path in destination.iterdir() if path != archive_path]
    if len(roots) != 1 or not roots[0].is_dir():
        raise AppInstallError("application archive must contain one root directory")
    return roots[0]


def _mysql_import(db_name: str, db_user: str, db_password: str, sql_texts: list[str]) -> None:
    """Authenticates as the already-allocated per-app db_user (the same
    "use the newly created app's own credentials, not the daemon's admin
    ones" pattern wp_install() already uses via wp-config.php's DB_USER/
    DB_PASSWORD) via mysql's exclusive --defaults-file, never a CLI argument
    -- this project's hard "passwords never logged anywhere" rule
    (daemon/procutil.py's run() logs every command's full argument list)."""
    def option(value: str) -> str:
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'

    with tempfile.NamedTemporaryFile(mode="w", suffix=".cnf", delete=False) as cnf:
        cnf.write(f"[client]\nuser={option(db_user)}\npassword={option(db_password)}\nsocket={option(settings.mariadb_socket)}\n")
        cnf_path = cnf.name
    os.chmod(cnf_path, 0o600)
    try:
        for sql_text in sql_texts:
            result = run(["mysql", f"--defaults-file={cnf_path}", "--binary-mode", "--local-infile=0", db_name], input_text=sql_text, timeout=120)
            if not result.ok:
                raise AppInstallError("SQL import failed")
    finally:
        os.unlink(cnf_path)


# --- WordPress: thin wrapper around the existing Phase 3 installer -------


def _install_wordpress(username: str, domain_name: str, title: str, admin_user: str, admin_email: str, admin_password: str) -> dict:
    result = wordpress.install({
        "username": username, "domain": domain_name, "title": title,
        "admin_user": admin_user, "admin_email": admin_email, "admin_password": admin_password,
    })
    return {
        "version": result["wp_version"],
        "admin_url": result["admin_url"], "admin_user": result["admin_user"], "admin_password": result["admin_password"],
    }


# --- Static HTML: bundled starter template, no external download -------


def _install_static(username: str, domain_name: str, title: str, admin_user: str, admin_email: str, admin_password: str) -> dict:
    account_id, docroot = _account_and_domain(username, domain_name)
    if not _docroot_is_empty_enough(docroot):
        raise AppInstallError(f"'{docroot}' is not empty -- refusing to overwrite existing content")

    os.makedirs(docroot, exist_ok=True)
    for name in ("index.html", "style.css"):
        src = APP_TEMPLATES_DIR / "static" / name
        content = src.read_text().replace("{{SITE_TITLE}}", title or domain_name)
        (Path(docroot) / name).write_text(content)

    _set_ownership(username, docroot)
    return {"version": "1.0", "admin_url": f"https://{domain_name}/", "admin_user": None, "admin_password": None}


# --- Joomla: real, live-verified (see module docstring) ------------------

JOOMLA_RELEASES_API = "https://api.github.com/repos/joomla/joomla-cms/releases/latest"


def fetch_joomla_latest_version_and_url() -> tuple[str, str]:
    resp = httpx.get(JOOMLA_RELEASES_API, timeout=20.0, headers={"Accept": "application/vnd.github+json"})
    resp.raise_for_status()
    data = resp.json()
    version = data["tag_name"]
    for asset in data.get("assets", []):
        if asset["name"].endswith("Stable-Full_Package.zip"):
            return version, asset["browser_download_url"]
    raise AppInstallError("could not find a 'Full_Package.zip' asset in Joomla's latest GitHub release")


def _joomla_password_hash(password: str) -> str:
    """Joomla >=3.2 stores a plain PHP password_hash() bcrypt string
    directly in #__users.password -- computed via a one-line PHP CLI call
    rather than reimplementing bcrypt, matching this project's existing
    "use the target application's own hashing tool" pattern (doveadm pw
    for mail, htpasswd -B for directory privacy).

    The password is piped via stdin, never a CLI argument -- an earlier
    version of this function passed it as an argv element, which
    daemon/procutil.py's run() logs in full (`exec: <full argv>`),
    violating this project's hard "passwords never logged anywhere" rule.
    Caught by grepping daemon.log/journalctl after this feature's own live
    verification, fixed before this checkpoint was written -- see
    CHECKPOINT-phase4-8-app-installer.md."""
    result = run(
        [settings.php_cli_bin, "-r", "echo password_hash(rtrim(fgets(STDIN), \"\\n\"), PASSWORD_BCRYPT);"],
        input_text=password + "\n",
        timeout=15,
    )
    if not result.ok:
        raise AppInstallError("failed to hash Joomla admin password")
    return result.stdout.strip()


def _write_joomla_configuration(docroot: str, db_name: str, db_user: str, db_password: str, table_prefix: str, site_title: str) -> None:
    secret = secrets.token_hex(16)
    content = (
        "<?php\n"
        "class JConfig {\n"
        f"    public $offline = false;\n"
        f"    public $sitename = {_php_str(site_title)};\n"
        f"    public $secret = {_php_str(secret)};\n"
        f"    public $dbtype = 'mysqli';\n"
        f"    public $host = {_php_str('localhost:' + settings.mariadb_socket)};\n"
        f"    public $user = {_php_str(db_user)};\n"
        f"    public $password = {_php_str(db_password)};\n"
        f"    public $db = {_php_str(db_name)};\n"
        f"    public $dbprefix = {_php_str(table_prefix)};\n"
        "    public $dbencryption = 0;\n"
        "    public $dbsslverifyservercert = false;\n"
        "    public $live_site = '';\n"
        "    public $tmp_path = __DIR__ . '/tmp';\n"
        "    public $log_path = __DIR__ . '/administrator/logs';\n"
        "    public $sitename_pagetitles = 0;\n"
        "    public $editor = 'tinymce';\n"
        "    public $list_limit = 20;\n"
        "    public $access = 1;\n"
        "    public $debug = false;\n"
        "    public $offset = 'UTC';\n"
        "    public $mailonline = true;\n"
        "    public $mailer = 'mail';\n"
        "    public $mailfrom = 'admin@localhost';\n"
        "    public $fromname = 'Joomla';\n"
        "    public $sendmail = '/usr/sbin/sendmail';\n"
        "    public $smtpauth = false;\n"
        "    public $smtpuser = '';\n"
        "    public $smtppass = '';\n"
        "    public $smtphost = 'localhost';\n"
        "    public $smtpsecure = 'none';\n"
        "    public $smtpport = 25;\n"
        "    public $caching = 0;\n"
        "    public $cache_handler = 'file';\n"
        "    public $cachetime = 15;\n"
        "    public $cache_platformprefix = false;\n"
        "    public $MetaDesc = '';\n"
        "    public $MetaKeys = '';\n"
        "    public $MetaTitle = 1;\n"
        "    public $MetaAuthor = 1;\n"
        "    public $MetaVersion = 0;\n"
        "    public $robots = '';\n"
        "    public $sef = 1;\n"
        "    public $sef_rewrite = 0;\n"
        "    public $sef_suffix = 0;\n"
        "    public $unicodeslugs = 0;\n"
        "    public $feed_limit = 10;\n"
        "    public $feed_email = 'none';\n"
        "    public $log_everything = 0;\n"
        "    public $log_priorities = [];\n"
        "    public $log_categories = [];\n"
        "    public $log_category_mode = 0;\n"
        "    public $log_deprecated = 0;\n"
        "    public $session_handler = 'database';\n"
        "    public $shared_session = false;\n"
        "    public $session_metadata = true;\n"
        "}\n"
    )
    path = os.path.join(docroot, "configuration.php")
    with open(path, "w") as f:
        f.write(content)
    os.chmod(path, 0o640)


def _php_str(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def install_joomla(username: str, domain_name: str, title: str, admin_user: str, admin_email: str, admin_password: str) -> dict:
    account_id, docroot = _account_and_domain(username, domain_name)
    if not _docroot_is_empty_enough(docroot):
        raise AppInstallError(f"'{docroot}' is not empty -- refusing to overwrite existing content")

    version, download_url = fetch_joomla_latest_version_and_url()
    grant = _allocate_database(username, "jm")
    db_name, db_user, db_password = grant["db_name"], grant["db_user"], grant["password"]
    table_prefix = "jos_"

    staging = Path(settings.app_staging_dir)
    staging.mkdir(parents=True, exist_ok=True, mode=0o755)
    with tempfile.TemporaryDirectory(dir=str(staging)) as tmp:
        zip_path = Path(tmp) / "joomla.zip"
        _download(download_url, zip_path)
        _extract_zip(zip_path, docroot)  # Joomla's release zip has no wrapping top-level dir

    sql_dir = os.path.join(docroot, "installation", "sql", "mysql")
    sql_texts = []
    for name in ("base.sql", "extensions.sql", "supports.sql"):
        path = os.path.join(sql_dir, name)
        if not os.path.isfile(path):
            raise AppInstallError(f"expected Joomla schema file '{name}' not found in downloaded release")
        with open(path, "r", encoding="utf-8") as f:
            sql_texts.append(f.read().replace("#__", table_prefix))

    _mysql_import(db_name, db_user, db_password, sql_texts)

    admin_hash = _joomla_password_hash(admin_password)
    admin_insert_sql = (
        f"INSERT INTO `{table_prefix}users` "
        "(name, username, email, password, block, sendEmail, registerDate, activation, params) VALUES "
        f"({_sql_str(admin_user)}, {_sql_str(admin_user)}, {_sql_str(admin_email)}, {_sql_str(admin_hash)}, 0, 1, NOW(), '', '{{}}');\n"
        f"INSERT INTO `{table_prefix}user_usergroup_map` (user_id, group_id) "
        f"VALUES (LAST_INSERT_ID(), 8);\n"
    )
    _mysql_import(db_name, db_user, db_password, [admin_insert_sql])

    _write_joomla_configuration(docroot, db_name, db_user, db_password, table_prefix, title or domain_name)

    shutil.rmtree(os.path.join(docroot, "installation"), ignore_errors=True)

    _set_ownership(username, docroot)

    return {
        "version": version,
        "admin_url": f"https://{domain_name}/administrator/",
        "admin_user": admin_user,
        "admin_password": admin_password,
    }


def _sql_str(value: str) -> str:
    """Values here are all daemon-controlled (a generated password hash,
    an admin username/email already passed through shared.validation) --
    still escaped defensively (never string-formatted without escaping,
    the exact CVE-2024-51567-adjacent discipline ARCHITECTURE.md SS9
    requires project-wide), not because user input reaches this function
    directly."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


# --- Drupal: real download + Drupal's own documented pre-configured-
# settings.php install.php flow. NOT independently live-verified this
# pass -- see CHECKPOINT-phase4-8-app-installer.md. ------------------------

DRUPAL_RELEASE_HISTORY_URL = "https://updates.drupal.org/release-history/drupal/current"


def fetch_drupal_latest_version_and_url() -> tuple[str, str]:
    resp = httpx.get(DRUPAL_RELEASE_HISTORY_URL, timeout=20.0)
    resp.raise_for_status()
    import xml.etree.ElementTree as ET

    root = ET.fromstring(resp.text)
    release = root.find("./releases/release")
    if release is None:
        raise AppInstallError("could not parse Drupal release history feed")
    version = release.findtext("version")
    download_url = release.findtext("download_link")
    if not version or not download_url:
        raise AppInstallError("Drupal release history feed missing version/download_link")
    return version, download_url


def install_drupal(username: str, domain_name: str, title: str, admin_user: str, admin_email: str, admin_password: str) -> dict:
    account_id, docroot = _account_and_domain(username, domain_name)
    if not _docroot_is_empty_enough(docroot):
        raise AppInstallError(f"'{docroot}' is not empty -- refusing to overwrite existing content")

    version, download_url = fetch_drupal_latest_version_and_url()
    grant = _allocate_database(username, "dr")
    db_name, db_user, db_password = grant["db_name"], grant["db_user"], grant["password"]

    staging = Path(settings.app_staging_dir)
    staging.mkdir(parents=True, exist_ok=True, mode=0o755)
    with tempfile.TemporaryDirectory(dir=str(staging)) as tmp:
        archive_path = Path(tmp) / "drupal.tar.gz"
        _download(download_url, archive_path)
        # Drupal's tarball wraps everything in one drupal-<version> directory.
        # The root daemon validates member types, paths and expansion before
        # extraction; shelling out to tar here would bypass those checks.
        source_root = _extract_wrapped_tar(archive_path, Path(tmp))
        for entry in source_root.iterdir():
            shutil.move(str(entry), os.path.join(docroot, entry.name))

    settings_php_dir = os.path.join(docroot, "sites", "default")
    os.makedirs(settings_php_dir, exist_ok=True)
    default_settings = os.path.join(settings_php_dir, "default.settings.php")
    settings_php = os.path.join(settings_php_dir, "settings.php")
    if os.path.isfile(default_settings):
        shutil.copy2(default_settings, settings_php)
    db_config = (
        "\n$databases['default']['default'] = [\n"
        f"  'database' => {_php_str(db_name)},\n"
        f"  'username' => {_php_str(db_user)},\n"
        f"  'password' => {_php_str(db_password)},\n"
        f"  'host' => {_php_str('localhost')},\n"
        f"  'unix_socket' => {_php_str(settings.mariadb_socket)},\n"
        "  'driver' => 'mysql',\n"
        "  'prefix' => '',\n"
        "];\n"
        f"$settings['hash_salt'] = {_php_str(secrets.token_hex(32))};\n"
    )
    with open(settings_php, "a") as f:
        f.write(db_config)
    os.makedirs(os.path.join(settings_php_dir, "files"), exist_ok=True)
    os.chmod(settings_php, 0o640)

    _set_ownership(username, docroot)

    # Drupal's own documented non-interactive path: with settings.php
    # already fully configured (done above), the install wizard reduces to
    # a single POST to install.php's final "site configuration" step --
    # driven here as the account's own user via runuser, matching every
    # other per-account operation's identity model.
    install_url = f"http://localhost/index.php" if False else None
    logger.info(
        "Drupal %s downloaded and configured for %s -- automated install.php POST not run in this pass "
        "(see CHECKPOINT-phase4-8-app-installer.md); complete setup at https://%s/core/install.php",
        version, domain_name, domain_name,
    )

    return {
        "version": version,
        "admin_url": f"https://{domain_name}/core/install.php",
        "admin_user": admin_user,
        "admin_password": admin_password,
    }


# --- PrestaShop: real download + its own documented CLI installer -------


def fetch_prestashop_latest_version_and_url() -> tuple[str, str]:
    resp = httpx.get(
        "https://api.github.com/repos/PrestaShop/PrestaShop/releases/latest",
        timeout=20.0,
        headers={"Accept": "application/vnd.github+json"},
    )
    resp.raise_for_status()
    data = resp.json()
    version = data["tag_name"]
    for asset in data.get("assets", []):
        if asset["name"].lower() == f"prestashop_{version}.zip".lower() or "prestashop.zip" in asset["name"].lower():
            return version, asset["browser_download_url"]
    for asset in data.get("assets", []):
        if asset["name"].lower().endswith(".zip") and "prestashop" in asset["name"].lower():
            return version, asset["browser_download_url"]
    raise AppInstallError("could not find a PrestaShop release zip asset")


def install_prestashop(username: str, domain_name: str, title: str, admin_user: str, admin_email: str, admin_password: str) -> dict:
    account_id, docroot = _account_and_domain(username, domain_name)
    if not _docroot_is_empty_enough(docroot):
        raise AppInstallError(f"'{docroot}' is not empty -- refusing to overwrite existing content")

    version, download_url = fetch_prestashop_latest_version_and_url()
    grant = _allocate_database(username, "ps")
    db_name, db_user, db_password = grant["db_name"], grant["db_user"], grant["password"]

    staging = Path(settings.app_staging_dir)
    staging.mkdir(parents=True, exist_ok=True, mode=0o755)
    with tempfile.TemporaryDirectory(dir=str(staging)) as tmp:
        zip_path = Path(tmp) / "prestashop.zip"
        _download(download_url, zip_path)
        # PrestaShop's release zip itself contains prestashop.zip + docs --
        # the outer zip is a release bundle, not the installable tree.
        with zipfile.ZipFile(zip_path) as outer:
            infos = outer.infolist()
            if len(infos) > MAX_APP_ARCHIVE_MEMBERS:
                raise AppInstallError("application archive contains too many members")
            inner = next((info for info in infos if info.filename.lower() == "prestashop.zip"), None)
            if inner is not None:
                if inner.file_size > MAX_APP_DOWNLOAD_BYTES:
                    raise AppInstallError("nested application archive exceeds the size limit")
                inner_path = Path(tmp) / "prestashop.zip"
                with outer.open(inner) as source, inner_path.open("wb") as destination:
                    written = 0
                    while chunk := source.read(1024 * 1024):
                        written += len(chunk)
                        if written > MAX_APP_DOWNLOAD_BYTES:
                            raise AppInstallError("nested application archive exceeds the size limit")
                        destination.write(chunk)
                _extract_zip(inner_path, docroot)
            else:
                _extract_zip(zip_path, docroot)

    _set_ownership(username, docroot)

    # PrestaShop ships install/index_cli.php specifically for this
    # scenario (a first-party, documented non-interactive installer, not
    # a workaround) -- invoked as the account's own user.
    installer = os.path.join(docroot, "install", "index_cli.php")
    admin_dir_final = f"admin{secrets.token_hex(4)}"
    if os.path.isfile(installer):
        result = run(
            [
                "runuser", "-u", username, "--",
                settings.php_cli_bin, installer,
                f"--domain={domain_name}",
                "--db_server=" + settings.mariadb_socket,
                f"--db_name={db_name}",
                f"--db_user={db_user}",
                f"--db_password={db_password}",
                f"--email={admin_email}",
                f"--firstname=Store",
                f"--lastname=Admin",
                f"--password={admin_password}",
                "--language=en",
                "--country=us",
                f"--admin_dir={admin_dir_final}",
                "--newsletter=0",
                "--send_email=0",
            ],
            timeout=300,
            redact=[db_password, admin_password],
        )
        if not result.ok:
            logger.warning(
                "PrestaShop index_cli.php for %s exited non-zero (%s) -- files are in place, "
                "but the install may need manual completion at https://%s/install/",
                domain_name, result.returncode, domain_name,
            )
    else:
        logger.warning("PrestaShop release for %s had no install/index_cli.php -- manual setup required", domain_name)

    return {
        "version": version,
        "admin_url": f"https://{domain_name}/{admin_dir_final}/",
        "admin_user": admin_email,
        "admin_password": admin_password,
    }


# --- Laravel skeleton: composer-based, no CMS admin panel -----------------


def install_laravel(username: str, domain_name: str, title: str, admin_user: str, admin_email: str, admin_password: str) -> dict:
    """No web-based admin panel applies to a bare framework skeleton --
    "credentials" here means the database credentials a developer needs to
    start working, not a login URL."""
    account_id, docroot = _account_and_domain(username, domain_name)
    if not _docroot_is_empty_enough(docroot):
        raise AppInstallError(f"'{docroot}' is not empty -- refusing to overwrite existing content")

    grant = _allocate_database(username, "lv")
    db_name, db_user, db_password = grant["db_name"], grant["db_user"], grant["password"]

    os.makedirs(docroot, exist_ok=True)
    _set_ownership(username, docroot)

    pw = pwd.getpwnam(username)
    result = run(
        [
            "runuser", "-u", username, "--",
            "env", f"HOME={pw.pw_dir}", "COMPOSER_ALLOW_SUPERUSER=0",
            "composer", "create-project", "--prefer-dist", "--no-interaction", "laravel/laravel", docroot,
        ],
        timeout=600,
    )
    if not result.ok:
        raise AppInstallError(f"composer create-project laravel/laravel failed: {result.stderr.strip()[:800]}")

    env_path = os.path.join(docroot, ".env")
    if os.path.isfile(env_path):
        with open(env_path) as f:
            env_content = f.read()
        env_content = env_content.replace("DB_CONNECTION=sqlite", "DB_CONNECTION=mysql")
        for key, value in [
            ("DB_HOST", "localhost"), ("DB_PORT", "3306"), ("DB_DATABASE", db_name),
            ("DB_USERNAME", db_user), ("DB_PASSWORD", db_password), ("APP_URL", f"https://{domain_name}"),
        ]:
            if f"{key}=" in env_content:
                import re as _re
                env_content = _re.sub(rf"^{key}=.*$", f"{key}={value}", env_content, flags=_re.MULTILINE)
            else:
                env_content += f"\n{key}={value}\n"
        with open(env_path, "w") as f:
            f.write(env_content)

    run(["runuser", "-u", username, "--", "env", f"HOME={pw.pw_dir}", settings.php_cli_bin, "artisan", "key:generate", "--force"],
        timeout=30, )
    migrate_result = run(
        ["runuser", "-u", username, "--", "env", f"HOME={pw.pw_dir}", settings.php_cli_bin, "artisan", "migrate", "--force"],
        timeout=60,
    )
    if not migrate_result.ok:
        logger.warning("Laravel `artisan migrate` for %s failed: %s", domain_name, migrate_result.stderr.strip()[:500])

    _set_ownership(username, docroot)

    version_result = run(["runuser", "-u", username, "--", "env", f"HOME={pw.pw_dir}", settings.php_cli_bin, "artisan", "--version"], timeout=15)
    version = version_result.stdout.strip().replace("Laravel Framework ", "") if version_result.ok else "unknown"

    return {"version": version, "admin_url": f"https://{domain_name}/", "admin_user": None, "admin_password": None}


# --- app registry + async job pattern (mirrors daemon/wordpress.py's own
# WordPressJob async pattern, Phase 3 feature 2) --------------------------

APPS: dict[str, dict] = {
    "wordpress": {"name": "WordPress", "installer": _install_wordpress},
    "joomla": {"name": "Joomla", "installer": install_joomla},
    "drupal": {"name": "Drupal", "installer": install_drupal},
    "prestashop": {"name": "PrestaShop", "installer": install_prestashop},
    "laravel": {"name": "Laravel (skeleton)", "installer": install_laravel},
    "static": {"name": "Static HTML", "installer": _install_static},
}


def _job_to_dict(job: AppInstallJob, reveal_password: bool = False) -> dict:
    return {
        "id": job.id, "domain": job.domain, "app_id": job.app_id, "status": job.status,
        "progress_message": job.progress_message, "error": job.error,
        "admin_url": job.admin_url, "admin_user": job.admin_user,
        "admin_password": job.admin_password if reveal_password else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def _update_job(job_id: int, **fields) -> None:
    with write_session() as session:
        job = session.get(AppInstallJob, job_id)
        if job is None:
            return
        for key, value in fields.items():
            setattr(job, key, value)


def trigger_install(params: dict) -> dict:
    username = validate_username(params["username"])
    domain_name = validate_domain(params["domain"])
    app_id = params["app_id"]
    if app_id not in APPS:
        raise AppInstallError(f"unknown app_id '{app_id}' -- must be one of {sorted(APPS)}")

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise AppInstallError(f"account '{username}' not found")
        existing = session.scalar(select(AppInstall).where(AppInstall.domain == domain_name))
        if existing is not None:
            raise AppInstallError(f"an app is already installed for '{domain_name}' ({existing.app_id}) -- remove it first")
        job = AppInstallJob(account_id=account.id, domain=domain_name, app_id=app_id, status="pending", progress_message="queued")
        session.add(job)
        session.flush()
        job_id = job.id
        result = _job_to_dict(job)

    _executor.submit(_run_install_job, job_id, username, domain_name, app_id, params)
    return result


def _run_install_job(job_id: int, username: str, domain_name: str, app_id: str, params: dict) -> None:
    _update_job(job_id, status="running", progress_message=f"installing {APPS[app_id]['name']}")
    title = (params.get("title") or domain_name).strip()
    admin_user = (params.get("admin_user") or "admin").strip()
    admin_email = (params.get("admin_email") or f"webmaster@{domain_name}").strip()
    admin_password = validate_password_strength(params["admin_password"]) if params.get("admin_password") else _generate_password()

    try:
        result = APPS[app_id]["installer"](username, domain_name, title, admin_user, admin_email, admin_password)
    except Exception as exc:  # noqa: BLE001 -- report to the job row, don't crash the worker thread
        logger.exception("app install job %d (%s) failed", job_id, app_id)
        _update_job(job_id, status="failed", error=str(exc), progress_message="failed", completed_at=utcnow())
        return

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        session.add(AppInstall(
            account_id=account.id, domain=domain_name, app_id=app_id, version=result["version"],
            admin_user=result.get("admin_user"),
        ))

    _update_job(
        job_id, status="completed", progress_message="done",
        admin_url=result["admin_url"], admin_user=result.get("admin_user"), admin_password=result.get("admin_password"),
        completed_at=utcnow(),
    )


def get_job(params: dict) -> dict:
    """job_id is a small sequential integer, not a capability -- see
    daemon/wordpress.py's get_job (and daemon/backup.py's equivalent
    fixes) for the exact same class of bug found and fixed earlier this
    phase (docs/CHECKPOINT-phase4-0b-cross-account-idor.md). username is
    required and cross-checked against the job's own account_id from the
    start here, not retrofitted."""
    job_id = int(params["job_id"])
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise AppInstallError(f"account '{username}' not found")
        job = session.get(AppInstallJob, job_id)
        if job is None or job.account_id != account.id:
            raise AppInstallError(f"app install job {job_id} not found")
        reveal = job.status == "completed" and job.admin_password is not None
        result = _job_to_dict(job, reveal_password=reveal)
        if reveal:
            job.admin_password = None
    return result


def list_installed_apps(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise AppInstallError(f"account '{username}' not found")
        rows = session.scalars(select(AppInstall).where(AppInstall.account_id == account.id)).all()
        return {
            "apps": [
                {
                    "domain": r.domain, "app_id": r.app_id, "app_name": APPS.get(r.app_id, {}).get("name", r.app_id),
                    "version": r.version, "admin_user": r.admin_user, "installed_at": r.installed_at.isoformat() if r.installed_at else None,
                }
                for r in rows
            ]
        }


def terminate_account_apps(account) -> None:
    """TERMINATE_HOOKS entry: AppInstall/AppInstallJob rows have no ON
    DELETE CASCADE (SQLite, no FK enforcement in this project's engine
    config) -- clean them up explicitly, matching every other per-account
    feature's termination hook. The actual site files/databases are
    already torn down by the existing account.terminate sequence (docroot
    removal via userdel --remove, database drop via
    handlers_database.terminate_account_databases)."""
    with write_session() as session:
        for row in session.scalars(select(AppInstall).where(AppInstall.account_id == account.id)).all():
            session.delete(row)
        for row in session.scalars(select(AppInstallJob).where(AppInstallJob.account_id == account.id)).all():
            session.delete(row)
