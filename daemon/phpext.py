"""Per-account PHP extension enable/disable.

How it works: every lsphp build on this box is compiled with
`--with-config-file-scan-dir=<prefix>/etc/php/<ver>/mods-available/` -- the
shared, server-wide directory of `extension=foo.so` ini snippets that
decides which extensions load. PHP also honors a PHP_INI_SCAN_DIR
environment variable that *replaces* that compiled-in directory, and OLS
extProcessor blocks can set env vars per account. So: the daemon
materializes a per-account, per-version scan directory populated with
symlinks to exactly the stock ini snippets that account wants enabled, and
the account's own extProcessor gets `env PHP_INI_SCAN_DIR=<that dir>`
(httpd_config.conf.j2). No other account's PHP is affected, and an account
with no PhpExtensionSet row keeps the stock compiled-in behavior entirely.

Placement and ownership are both load-bearing:
- The scan dir lives at phpdirectives.PHP_SCAN_ROOT/<user>/<ver>/conf.d
  (/usr/local/lib/boron/php-scan/...). Namespaced accounts only see their
  own home plus a few ro-bound system trees inside the jail; /etc/boron was
  confirmed invisible from a live jailed lsphp, but /usr is ro-bound by both
  the native OLS namespace and Bubblewrap (the symlink TARGETS under
  /usr/local/lsws live there too).
- Every component is root-owned and outside the account's writable tree.
  The previous location (~/.php under the HOME) was root-owned too, but the
  home itself belongs to the account (no sticky bit), so the account could
  rename ~/.php away and recreate it with its own ini files -- which lsphp
  loads at startup as system-level config (bypassing disable_functions and
  the extension policy). bootstrap_all_php_extensions migrates old trees.

Extension inventory is discovered from disk per version, never hardcoded:
the installed .so files differ between lsphp builds (8.1 ships only four,
8.5 has no opcache.so), so what's toggleable is whatever this box really
has. Enabling a .so that ships no stock ini snippet generates a one-line
snippet instead (extension=/zend_extension= by name).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import stat
from pathlib import Path

from daemon import account_mutation
from daemon.safeio import open_dir_beneath, secure_mkdirs, _replace_file_at_fd, UnsafePathError

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, PhpExtensionSet, utcnow
from shared.validation import ValidationError, validate_username

from daemon import ols, phpdirectives, sysops
from daemon.phpdirectives import php_scan_dir

logger = logging.getLogger("borond.phpext")

LSWS_BASE = "/usr/local/lsws"

# Extension names come from stock package filenames, but they still get
# re-validated against this before ever reaching a filesystem path or the
# enabled list -- defense in depth, not the primary control.
EXT_NAME_RE = re.compile(r"\A[a-z0-9_]{1,32}\Z")

# The only extension on this box that must load as zend_extension= rather
# than extension= (its stock snippet does the same).
ZEND_EXTENSIONS = {"opcache"}

# Inter-extension load dependencies, discovered empirically (each snippet
# loaded solo via PHP_INI_SCAN_DIR, warnings checked): this box's phpredis
# is built with the igbinary serializer, so redis refuses to load without
# it -- the reason the stock snippets carry 40-/50- load-order prefixes.
# Enforced as a validation error rather than silently auto-fixing: an
# inconsistent set would otherwise "save fine" but log a load warning and
# leave the extension missing.
EXTENSION_DEPS = {"redis": ("igbinary",)}


def _mods_available_dir(php_version: str) -> Path:
    nodot = php_version.replace(".", "")
    return Path(f"{LSWS_BASE}/lsphp{nodot}/etc/php/{php_version}/mods-available")


def _so_names(php_version: str) -> set[str]:
    """Installed shared-object extensions for one lsphp build. The .so files
    live in lib/php/<API-date>/ where the date differs per build -- glob it
    rather than hardcoding five magic numbers."""
    nodot = php_version.replace(".", "")
    names: set[str] = set()
    for api_dir in Path(f"{LSWS_BASE}/lsphp{nodot}/lib/php").glob("*"):
        for so in api_dir.glob("*.so"):
            if EXT_NAME_RE.match(so.stem):
                names.add(so.stem)
    return names


def _stock_inis(php_version: str) -> dict[str, Path]:
    """name -> stock ini snippet path. '50-redis.ini' style numeric load-
    order prefixes are stripped for the name but the full filename is kept
    when symlinking, so relative load order is preserved."""
    result: dict[str, Path] = {}
    mods = _mods_available_dir(php_version)
    if not mods.is_dir():
        return result
    for ini in sorted(mods.glob("*.ini")):
        name = re.sub(r"\A\d+-", "", ini.stem)
        if EXT_NAME_RE.match(name):
            result[name] = ini
    return result


def inventory() -> dict[str, dict]:
    """Every toggleable extension across all installed PHP versions:
    name -> {"versions": [...], "stock_enabled": bool}. stock_enabled means
    the server default loads it (a stock ini snippet exists for at least
    one version) -- what an account without an override row gets."""
    result: dict[str, dict] = {}
    for version in settings.php_versions:
        stock = _stock_inis(version)
        for name in _so_names(version):
            entry = result.setdefault(name, {"versions": [], "stock_enabled": False})
            entry["versions"].append(version)
            if name in stock:
                entry["stock_enabled"] = True
    return dict(sorted(result.items()))


def stock_enabled_names() -> list[str]:
    return sorted(name for name, meta in inventory().items() if meta["stock_enabled"])


def _materialize(username: str) -> None:
    """(Re)build this account's scan dirs for EVERY installed PHP version,
    not just the one currently in use -- a later per-domain version switch
    (Phase 7a feature 6) must find its dir already present, since the
    extProcessor's PHP_INI_SCAN_DIR points there unconditionally once the
    account has a row. Wipe-and-recreate keeps it exactly in sync with the
    enabled list (no stale snippets from a previously-enabled extension)."""
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        row = session.scalar(select(PhpExtensionSet).where(PhpExtensionSet.account_id == account.id))
        enabled = list(row.enabled) if row else None
    if enabled is None:
        return

    root = _scan_root()
    username = validate_username(username)
    for version in settings.php_versions:
        relative = f"{username}/{version.replace('.', '')}/conf.d"
        stock = _stock_inis(version)
        so_names = _so_names(version)
        secure_mkdirs(root, relative, 0, 0, 0o755)
        directory_fd = open_dir_beneath(root, relative)
        try:
            # This directory contains only snippets. Never recursively walk
            # attacker-supplied subdirectories or follow any path component.
            names = []
            with os.scandir(directory_fd) as entries:
                for entry in entries:
                    if len(names) >= 1024:
                        raise UnsafePathError("PHP scan directory has too many entries")
                    names.append(entry.name)
            for name in names:
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    raise UnsafePathError("PHP scan directory contains an unexpected directory")
            for name in names:
                os.unlink(name, dir_fd=directory_fd)
            for name in enabled:
                if name in stock:
                    os.symlink(str(stock[name]), stock[name].name, dir_fd=directory_fd)
                elif name in so_names:
                    directive = "zend_extension" if name in ZEND_EXTENSIONS else "extension"
                    _replace_file_at_fd(directory_fd, f"{name}.ini",
                        f"{directive}={name}.so\n".encode(), 0, 0, 0o644)
        finally:
            os.close(directory_fd)


def _scan_root() -> str:
    """Create (if needed) and verify the shared root-owned scan root.

    It and its parent must be real directories owned by root and not
    writable by group/other; otherwise someone other than root could swap
    an account's scan directory. Mode 0711 stops accounts listing which
    other accounts have an override while still letting lsphp traverse."""
    root = phpdirectives.PHP_SCAN_ROOT
    os.makedirs(root, mode=0o755, exist_ok=True)
    # Explicit modes: borond runs with UMask=0027, which would otherwise
    # leave the parent untraversable for the account-uid lsphp workers.
    for path, mode in ((os.path.dirname(root), 0o755), (root, 0o711)):
        try:
            fd = open_dir_beneath(path)
        except OSError as exc:
            raise UnsafePathError(f"PHP scan root component '{path}' is not a real directory") from exc
        try:
            info = os.fstat(fd)
            if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                raise UnsafePathError(f"PHP scan root component '{path}' is not root-owned and private")
            if mode is not None and stat.S_IMODE(info.st_mode) != mode:
                os.fchmod(fd, mode)
        finally:
            os.close(fd)
    return root


def _remove_entry_at(parent: str, name: str, *, root_owned_only: bool) -> bool:
    """Remove ``parent/name`` without following symlinks. Returns whether
    something was removed. ``root_owned_only`` leaves an entry the account
    itself created alone (it is no longer referenced by any config)."""
    try:
        parent_fd = open_dir_beneath(parent)
    except (FileNotFoundError, UnsafePathError):
        return False
    try:
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if root_owned_only and info.st_uid != 0:
            logger.warning("leaving account-owned '%s/%s' in place", parent, name)
            return False
        if stat.S_ISDIR(info.st_mode):
            if not shutil.rmtree.avoids_symlink_attacks:
                raise UnsafePathError("safe recursive removal is unavailable")
            shutil.rmtree(name, dir_fd=parent_fd)
        else:
            os.unlink(name, dir_fd=parent_fd)
        return True
    finally:
        os.close(parent_fd)


def _legacy_scan_tree_present(username: str) -> bool:
    """A root-owned pre-hardening ~/.php tree still exists for this account."""
    legacy = phpdirectives.legacy_php_scan_root(validate_username(username))
    try:
        info = os.lstat(legacy)
    except FileNotFoundError:
        return False
    return info.st_uid == 0


def _remove_legacy_scan_dirs(username: str) -> None:
    home = str(Path(settings.home_base) / validate_username(username))
    _remove_entry_at(home, '.php', root_owned_only=True)


def _remove_scan_dirs(username: str) -> None:
    username = validate_username(username)
    if os.path.isdir(phpdirectives.PHP_SCAN_ROOT):
        _remove_entry_at(_scan_root(), username, root_owned_only=False)
    _remove_legacy_scan_dirs(username)


def rename_scan_dirs(old: str, new: str) -> None:
    """Account rename: the scan dir no longer moves with the home, so move
    it explicitly (DB-independent, so it doubles as its own undo)."""
    old, new = validate_username(old), validate_username(new)
    if not os.path.isdir(phpdirectives.PHP_SCAN_ROOT):
        return
    root = _scan_root()
    root_fd = open_dir_beneath(root)
    try:
        try:
            info = os.stat(old, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(info.st_mode):
            raise UnsafePathError("PHP scan directory is not a real directory")
    finally:
        os.close(root_fd)
    _remove_entry_at(root, new, root_owned_only=False)
    root_fd = open_dir_beneath(root)
    try:
        os.rename(old, new, src_dir_fd=root_fd, dst_dir_fd=root_fd)
    finally:
        os.close(root_fd)


def _status(session, account: Account) -> dict:
    row = session.scalar(select(PhpExtensionSet).where(PhpExtensionSet.account_id == account.id))
    inv = inventory()
    stock = [name for name, meta in inv.items() if meta["stock_enabled"]]
    enabled = sorted(row.enabled) if row else stock
    return {
        "username": account.username,
        "overridden": row is not None,
        "enabled": enabled,
        "stock_enabled": stock,
        "extensions": [
            {
                "name": name,
                "enabled": name in enabled,
                "stock_enabled": meta["stock_enabled"],
                "versions": meta["versions"],
                "requires": list(EXTENSION_DEPS.get(name, ())),
            }
            for name, meta in inv.items()
        ],
        "updated_at": row.updated_at.isoformat() if row and row.updated_at else None,
    }


def list_extensions(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        return _status(session, account)


@account_mutation.locked
def set_extensions(params: dict) -> dict:
    username = validate_username(params["username"])
    requested = params.get("enabled")
    if not isinstance(requested, list):
        raise ValidationError("enabled must be a list of extension names")
    inv = inventory()
    enabled = sorted(set(requested))
    for name in enabled:
        if not isinstance(name, str) or name not in inv:
            raise ValidationError(f"'{name}' is not an installed PHP extension")
    for name in enabled:
        for dep in EXTENSION_DEPS.get(name, ()):
            if dep not in enabled:
                raise ValidationError(f"'{name}' requires '{dep}' to be enabled too")

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        if account.status not in ("active", "suspended"):
            raise RuntimeError(f"cannot set PHP extensions for an account in status '{account.status}'")
        row = session.scalar(select(PhpExtensionSet).where(PhpExtensionSet.account_id == account.id))
        if row is None:
            row = PhpExtensionSet(account_id=account.id, enabled=enabled)
            session.add(row)
        else:
            row.enabled = enabled
            row.updated_at = utcnow()
        account_snapshot = account

    # Order matters: the scan dirs must exist before the reload makes the
    # extProcessor's PHP_INI_SCAN_DIR point at them, or a worker spawned in
    # the gap would come up with zero extensions.
    _materialize(username)
    ols.refresh_vhost(account_snapshot)
    # env changes only reach NEW worker processes -- same already-warm-pool
    # reality sysops.recycle_php_workers documents for ini values.
    sysops.recycle_php_workers(username)

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        return _status(session, account)


@account_mutation.locked
def reset_extensions(params: dict) -> dict:
    """Back to stock: drop the row (so the extProcessor loses its
    PHP_INI_SCAN_DIR line and the compiled-in mods-available applies) and
    remove the now-orphaned ~/.php tree."""
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        row = session.scalar(select(PhpExtensionSet).where(PhpExtensionSet.account_id == account.id))
        if row is not None:
            session.delete(row)
        account_snapshot = account

    ols.refresh_vhost(account_snapshot)
    sysops.recycle_php_workers(username)
    _remove_scan_dirs(username)

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        return _status(session, account)


def terminate_account_php_extensions(account: Account) -> None:
    """TERMINATE_HOOKS entry: drop the row and the account's scan dirs
    (they live outside the home, so home removal no longer takes them).
    Idempotent."""
    with write_session() as session:
        row = session.scalar(select(PhpExtensionSet).where(PhpExtensionSet.account_id == account.id))
        if row is not None:
            session.delete(row)
    try:
        _remove_scan_dirs(account.username)
    except Exception:
        logger.exception("failed to remove PHP scan dirs for account '%s'", account.username)


def bootstrap_all_php_extensions() -> None:
    """Run once at borond startup (same category as
    redisacct.bootstrap_all_redis): re-materialize every overriding
    account's scan dirs, so a home dir restored from backup -- or a fresh
    lsphp version installed since -- can't leave PHP_INI_SCAN_DIR pointing
    at a missing directory (which would load ZERO extensions)."""
    with write_session() as session:
        usernames = [
            account.username
            for (account,) in session.execute(
                select(Account).join(PhpExtensionSet, PhpExtensionSet.account_id == Account.id)
            ).all()
            if account.status in ("active", "suspended")
        ]
    materialized = []
    for username in usernames:
        try:
            _materialize(username)
            materialized.append(username)
        except Exception:
            logger.exception("failed to materialize PHP scan dirs for account '%s'", username)
    _migrate_legacy_scan_dirs(materialized)


def _migrate_legacy_scan_dirs(usernames: list[str]) -> None:
    """Upgrade path for the old ~/.php location. The new dirs already exist
    (materialized above); re-render httpd_config so every extProcessor's
    PHP_INI_SCAN_DIR points at them, restart the warm workers that still
    carry the old env, and only then delete the old root-owned trees. If
    the re-render fails the old trees stay, so PHP keeps its extensions.

    The trigger is the live httpd_config, not only the root-owned tree: an
    account that already swapped ~/.php for its own (the attack being
    closed) leaves no root-owned tree behind, yet its extProcessor would
    keep loading that account-written dir until some unrelated re-render."""
    legacy = [username for username in usernames if _legacy_scan_tree_present(username)]
    stale = _accounts_with_legacy_scan_env()
    if not legacy and not stale:
        return
    try:
        ols.refresh_main_config()
    except Exception:
        logger.exception("could not repoint PHP_INI_SCAN_DIR; legacy ~/.php trees kept")
        return
    for username in sorted(set(legacy) | stale):
        try:
            sysops.recycle_php_workers(username)
        except Exception:
            logger.exception("failed to recycle PHP workers for account '%s'", username)
    for username in legacy:
        try:
            _remove_legacy_scan_dirs(username)
        except Exception:
            logger.exception("failed to remove legacy ~/.php for account '%s'", username)


def _accounts_with_legacy_scan_env() -> set[str]:
    """Usernames whose rendered extProcessor still sets PHP_INI_SCAN_DIR to
    the pre-hardening ~/.php location in the live httpd_config.conf."""
    try:
        text = Path(ols.HTTPD_CONFIG_PATH).read_text(errors="replace")
    except OSError:
        return set()
    pattern = re.compile(
        r"PHP_INI_SCAN_DIR=" + re.escape(settings.home_base.rstrip("/")) + r"/([a-z0-9_-]+)/\.php/"
    )
    return set(pattern.findall(text))
