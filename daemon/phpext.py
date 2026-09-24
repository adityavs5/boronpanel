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
- The scan dir lives under the account's HOME (~/.php/<ver>/conf.d), not
  /etc or /run -- namespaced accounts (nsisolation, the default) only see
  their own home tree inside the jail. /etc/boron was confirmed
  invisible from a live jailed lsphp (nsenter) before choosing this; the
  symlink TARGETS under /usr/local/lsws ARE visible in the jail (that's
  where lsphp itself runs from).
- Everything under ~/.php is root-owned (0755 dirs) -- the account can
  read but never write it, so it cannot inject arbitrary ini directives
  (which load at PHP startup as system-level config) into its own pool.

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

from daemon import ols, sysops
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

    home = str(Path(settings.home_base) / validate_username(username))
    for version in settings.php_versions:
        relative = f".php/{version.replace('.', '')}/conf.d"
        stock = _stock_inis(version)
        so_names = _so_names(version)
        secure_mkdirs(home, relative, 0, 0, 0o755)
        directory_fd = open_dir_beneath(home, relative)
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


def _remove_scan_dirs(username: str) -> None:
    home = str(Path(settings.home_base) / validate_username(username))
    try:
        home_fd = open_dir_beneath(home)
    except FileNotFoundError:
        return
    try:
        try:
            info = os.stat('.php', dir_fd=home_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISDIR(info.st_mode):
            if not shutil.rmtree.avoids_symlink_attacks:
                raise UnsafePathError("safe recursive removal is unavailable")
            shutil.rmtree('.php', dir_fd=home_fd)
        else:
            os.unlink('.php', dir_fd=home_fd)
    finally:
        os.close(home_fd)


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
    """TERMINATE_HOOKS entry: drop the row; the ~/.php tree goes with the
    home dir removal that termination already performs. Idempotent."""
    with write_session() as session:
        row = session.scalar(select(PhpExtensionSet).where(PhpExtensionSet.account_id == account.id))
        if row is not None:
            session.delete(row)


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
    for username in usernames:
        try:
            _materialize(username)
        except Exception:
            logger.exception("failed to materialize PHP scan dirs for account '%s'", username)
