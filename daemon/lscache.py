"""Phase 7a feature 4: per-domain LSCache.

Confirmed against this server's own installed OLS docs and module
definition (`/usr/local/lsws/docs/Module_Help.html`: "module parameters...
can override this setting at the Listener, Virtual Host, or Context
levels"; `/usr/local/lsws/modules/cache.def` lists the real, per-instance
parameter names this build's cache module accepts, including
`storagepath`/`noCacheUrl`/`expireInSeconds`) that OLS's built-in `cache`
module -- the mechanism branded "LiteSpeed Cache"/LSCache -- genuinely IS
overridable per virtual host, unlike ModSecurity (ARCHITECTURE.md SS10.5,
which has no per-vhost config at all). httpd_config.conf.j2's existing
server-level `module cache {}` block stays the global default
(`enableCache 0`, i.e. off unless a vhost opts in); enabling LSCache for a
domain renders a second, vhost-scoped `module cache {}` override block
inside that domain's own vhconf.conf (daemon/ols.py), which OLS's own
module-parameter inheritance rules apply on top of the server default.

Purge is implemented as a direct filesystem operation, not a live HTTP
round-trip through a `purgeUri`/special-header request: each cache-enabled
vhost gets its own explicit `storagepath` (a dedicated subdirectory of
this server's cache store, `/usr/local/lsws/cachedata/<vhost_name>/`) so
purging a domain's cache is simply removing that directory's contents --
deterministic, scriptable, and doesn't depend on the exact live HTTP
semantics of LiteSpeed's `purgeUri` parameter (which is *also* rendered,
as a documented secondary/manual mechanism, but not what the daemon's own
`purge()` relies on).
"""
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, Domain, LscacheSettings
from shared.validation import (
    validate_cache_ttl_seconds,
    validate_domain,
    validate_lscache_exclude_paths,
)

from daemon import ols
from daemon.safeio import open_dir_beneath, UnsafePathError

CACHE_STORE_ROOT = Path("/usr/local/lsws/cachedata")
WP_CONFIG_MARKER = "wp-config.php"
WP_LSCACHE_PLUGIN_MARKER = "wp-content/plugins/litespeed-cache/litespeed-cache.php"


def _get_domain_and_account(session, domain_name: str) -> tuple[Domain, Account]:
    domain_row = session.scalar(select(Domain).where(Domain.domain == domain_name))
    if domain_row is None:
        raise RuntimeError(f"domain '{domain_name}' not found")
    account = session.get(Account, domain_row.account_id)
    if account is None:
        raise RuntimeError(f"domain '{domain_name}' has no owning account")
    return domain_row, account


def _row_to_dict(row: LscacheSettings | None, domain_name: str) -> dict:
    if row is None:
        return {"domain": domain_name, "enabled": False, "ttl_seconds": 3600, "exclude_paths": [], "last_purged_at": None}
    return {
        "domain": row.domain,
        "enabled": row.enabled,
        "ttl_seconds": row.ttl_seconds,
        "exclude_paths": row.exclude_paths or [],
        "last_purged_at": row.last_purged_at.isoformat() if row.last_purged_at else None,
    }


def _wordpress_status(docroot: str) -> dict:
    wp_installed = os.path.isfile(os.path.join(docroot, WP_CONFIG_MARKER))
    plugin_installed = os.path.isfile(os.path.join(docroot, WP_LSCACHE_PLUGIN_MARKER))
    reminder = None
    if wp_installed and not plugin_installed:
        reminder = (
            "WordPress detected at this domain's docroot, but the LiteSpeed Cache plugin is not installed. "
            "Install it from wp-admin (Plugins > Add New > \"LiteSpeed Cache\") for full page-cache integration "
            "(cache-tag purging, ESI, WooCommerce/login-page exclusions) on top of the server-level cache this feature enables."
        )
    return {"wordpress_detected": wp_installed, "litespeed_cache_plugin_installed": plugin_installed, "plugin_reminder": reminder}


def get_settings(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    with write_session() as session:
        domain_row, _account = _get_domain_and_account(session, domain_name)
        row = session.scalar(select(LscacheSettings).where(LscacheSettings.domain == domain_name))
        result = _row_to_dict(row, domain_name)
        result.update(_wordpress_status(domain_row.docroot))
        return result


def set_settings(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    enabled = bool(params.get("enabled", False))
    ttl_seconds = validate_cache_ttl_seconds(params.get("ttl_seconds", 3600))
    exclude_paths = validate_lscache_exclude_paths(params.get("exclude_paths") or [])

    with write_session() as session:
        _domain_row, account = _get_domain_and_account(session, domain_name)
        row = session.scalar(select(LscacheSettings).where(LscacheSettings.domain == domain_name))
        if row is None:
            row = LscacheSettings(domain=domain_name)
            session.add(row)
        row.enabled = enabled
        row.ttl_seconds = ttl_seconds
        row.exclude_paths = exclude_paths
        session.flush()
        result = _row_to_dict(row, domain_name)
        account_snapshot = account

    ols.refresh_vhost(account_snapshot)
    return result


def _clear_storage_dir(domain_name: str) -> None:
    """Removes only the directory's CONTENTS, never the directory itself --
    a real bug found live during this feature's own verification: OLS's
    own worker process ("nobody", per httpd_config.conf.j2's top-level
    user/group) creates this directory itself the first time it writes a
    cache entry, with the setgid bit and group-write permission it needs
    to keep writing new entries later (confirmed live: `drwxrws---
    nobody nogroup`). An earlier version of this function did
    `shutil.rmtree(storage_dir)` followed by `storage_dir.mkdir(...)`,
    which recreates it as `root:root 0755` (borond's own identity) --
    "nobody" can then never write a new cache entry into it again, so
    every purge silently broke caching for that vhost's remaining
    lifetime, only fixable by deleting the directory outright and
    waiting for OLS to recreate it. If the directory doesn't exist yet
    at all (LSCache enabled but OLS never actually cached anything for
    this vhost), there's nothing to purge -- deliberately not created
    here either, for the identical reason. Shared by the operator-facing
    `purge()` RPC and the suspend/unsuspend hooks below -- both need the
    exact same filesystem operation, only the enabled-check and
    bookkeeping around it differ."""
    try:
        directory_fd = open_dir_beneath(str(cache_storage_path(domain_name)))
    except FileNotFoundError:
        return
    try:
        if not shutil.rmtree.avoids_symlink_attacks:
            raise UnsafePathError("safe cache removal is unavailable")
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                try:
                    info = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        shutil.rmtree(entry.name, dir_fd=directory_fd)
                    else:
                        os.unlink(entry.name, dir_fd=directory_fd)
                except FileNotFoundError:
                    continue
    finally:
        os.close(directory_fd)


def purge(params: dict) -> dict:
    """Removes every cached object for this domain's vhost -- a full
    purge, not selective by URL/tag (this build's OLS install has no
    LSCache tag-purge API wired up; the WordPress plugin's own tag-based
    purging, if installed, operates independently by sending its own
    purge signals that this same storagepath convention also honors)."""
    domain_name = validate_domain(params["domain"])
    with write_session() as session:
        _domain_row, _account = _get_domain_and_account(session, domain_name)
        row = session.scalar(select(LscacheSettings).where(LscacheSettings.domain == domain_name))
        if row is None or not row.enabled:
            raise RuntimeError(f"LSCache is not enabled for domain '{domain_name}'")

    _clear_storage_dir(domain_name)

    from shared.models import utcnow

    with write_session() as session:
        row = session.scalar(select(LscacheSettings).where(LscacheSettings.domain == domain_name))
        row.last_purged_at = utcnow()
        result = _row_to_dict(row, domain_name)
    return result


def purge_account_domains(account: Account) -> None:
    """SUSPEND_HOOKS / UNSUSPEND_HOOKS entry -- clears on-disk LSCache
    content for every domain this account owns, unconditionally (unlike
    `purge()`, deliberately does NOT require LscacheSettings.enabled: a
    page can still be sitting in the cache store from before LSCache was
    disabled). Idempotent (safe for accounts with no domains or that never
    used LSCache at all). Bookkeeping (`last_purged_at`) is intentionally
    skipped here -- that field means "last operator-initiated purge via
    the LSCache UI"; an automatic suspend/unsuspend purge is a different
    kind of event and updating it would be misleading in the UI.

    IMPORTANT SCOPE LIMIT (found live, on a real suspended site that kept
    serving cached content through this purge AND a full lshttpd restart):
    this only clears the per-vhost `storagepath` tree. Responses that
    carry their own `x-litespeed-cache-control` header (the LiteSpeed
    Cache WordPress plugin stamps `public, max-age=604800`) are cached by
    the server-level module into its DEFAULT storage path (top-level
    cachedata/), which this function cannot safely touch (it is shared
    across every vhost on the box). The *correctness* fix for suspension
    is therefore in templates/vhost.conf.j2: a suspended vhost renders an
    explicit `module cache` override with checkPublicCache/
    checkPrivateCache 0, disabling cache LOOKUPS entirely regardless of
    where stale objects physically live. This purge remains as hygiene
    for the per-vhost tree (disk reclaim + unsuspend freshness), not as
    the thing that makes suspension correct."""
    with write_session() as session:
        domains = list(session.scalars(select(Domain.domain).where(Domain.account_id == account.id)).all())
    for domain_name in domains:
        _clear_storage_dir(domain_name)


def get_stats(params: dict) -> dict:
    """Honest, verifiable proxy metrics -- not fabricated hit/miss
    counters this OLS build has no simple aggregate source for (per-
    response `X-LiteSpeed-Cache: hit|miss` headers exist, but there is no
    running aggregate counter exposed anywhere short of parsing every
    access log line with a custom log format this project doesn't
    otherwise use). Reports the real, current number of cached objects on
    disk and when the cache was last purged instead."""
    domain_name = validate_domain(params["domain"])
    with write_session() as session:
        _domain_row, _account = _get_domain_and_account(session, domain_name)
        row = session.scalar(select(LscacheSettings).where(LscacheSettings.domain == domain_name))

    cached_object_count = 0
    try:
        directory_fd = open_dir_beneath(str(cache_storage_path(domain_name)))
    except FileNotFoundError:
        directory_fd = None
    if directory_fd is not None:
        try:
            entries_seen = 0
            for relative, directories, files, current_fd in os.fwalk('.', dir_fd=directory_fd, follow_symlinks=False):
                if relative.count('/') > 64:
                    raise RuntimeError("cache statistics exceed the directory depth limit")
                entries_seen += len(directories) + len(files)
                if entries_seen > 200_000:
                    raise RuntimeError("cache statistics exceed the entry limit")
                for name in files:
                    try:
                        if stat.S_ISREG(os.stat(name, dir_fd=current_fd, follow_symlinks=False).st_mode):
                            cached_object_count += 1
                    except FileNotFoundError:
                        continue
        finally:
            os.close(directory_fd)

    return {
        "domain": domain_name,
        "cached_object_count": cached_object_count,
        "last_purged_at": row.last_purged_at.isoformat() if row and row.last_purged_at else None,
    }


def cache_storage_path(domain_name: str) -> Path:
    return CACHE_STORE_ROOT / ols._vhost_name(validate_domain(domain_name))


def delete_settings_for_domain(domain_name: str) -> None:
    """Called from handlers_domain.remove_domain -- this project's
    established manual-cascade convention (no DB-level ON DELETE CASCADE
    anywhere in this schema)."""
    with write_session() as session:
        row = session.scalar(select(LscacheSettings).where(LscacheSettings.domain == domain_name))
        if row is not None:
            session.delete(row)
    try:
        root_fd = open_dir_beneath(str(CACHE_STORE_ROOT))
    except FileNotFoundError:
        return
    try:
        name = cache_storage_path(domain_name).name
        try:
            info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                if not shutil.rmtree.avoids_symlink_attacks:
                    raise UnsafePathError("safe cache removal is unavailable")
                shutil.rmtree(name, dir_fd=root_fd)
            else:
                os.unlink(name, dir_fd=root_fd)
        except FileNotFoundError:
            pass
    finally:
        os.close(root_fd)


def terminate_account_lscache(account: Account) -> None:
    """TERMINATE_HOOKS entry -- an account's PRIMARY domain's Domain row
    (unlike addon/subdomain rows) survives termination, same established
    pattern handlers_redirect.terminate_account_redirects already
    documents, so remove_domain's own cleanup above never runs for it.
    Idempotent: safe even if the account never enabled LSCache for any
    domain."""
    with write_session() as session:
        domains = list(session.scalars(select(Domain.domain).where(Domain.account_id == account.id)).all())
    for domain_name in domains:
        delete_settings_for_domain(domain_name)
