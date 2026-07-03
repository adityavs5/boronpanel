"""Per-account PHP ini overrides (Phase 3 feature 6). Stored in a
dedicated PhpIniOverride row (see shared/models.py's docstring for why a
new table, not new Account columns) and rendered into that account's own
domain-vhosts only via daemon/ols.py's phpIniOverride block -- OLS's
native per-context PHP ini mechanism, so no other account's vhost is
ever affected and no system-wide php.ini file is touched.
"""
from __future__ import annotations

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, PhpIniOverride, utcnow
from shared.validation import (
    ValidationError,
    _php_size_to_mb,
    validate_php_error_reporting,
    validate_php_max_execution_time,
    validate_php_memory_limit,
    validate_php_size,
    validate_username,
)

from daemon import ols, sysops

DEFAULTS = {
    # Must match this server's real /usr/local/lsws/lsphp83/etc/php/8.3/litespeed/php.ini
    # -- these are shown in the UI/API as the account's *current* effective
    # values whenever no PhpIniOverride row exists yet (get_php_ini falls
    # back to this dict), so a mismatch here isn't cosmetic: a customer
    # would see e.g. "64M" upload limit pre-filled and not understand why
    # their actual uploads fail at 2M. Found stale during Phase 4 feature 9's
    # live verification (memory_limit/upload_max_filesize/post_max_size were
    # all higher than the real php.ini) -- confirmed against the live file,
    # not assumed, before correcting.
    "memory_limit": "128M",
    "upload_max_filesize": "2M",
    "post_max_size": "8M",
    "max_execution_time": 30,
    "display_errors": False,
    "error_reporting": "E_ALL & ~E_DEPRECATED & ~E_STRICT",
}


def _row_to_dict(row: PhpIniOverride) -> dict:
    return {
        "memory_limit": row.memory_limit,
        "upload_max_filesize": row.upload_max_filesize,
        "post_max_size": row.post_max_size,
        "max_execution_time": row.max_execution_time,
        "display_errors": row.display_errors,
        "error_reporting": row.error_reporting,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_php_ini(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        row = session.scalar(select(PhpIniOverride).where(PhpIniOverride.account_id == account.id))
        return {"username": username, "php_ini": _row_to_dict(row) if row else None, "defaults": DEFAULTS}


def set_php_ini(params: dict) -> dict:
    username = validate_username(params["username"])

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        if account.status not in ("active", "suspended"):
            raise RuntimeError(f"cannot set PHP ini overrides for an account in status '{account.status}'")
        row = session.scalar(select(PhpIniOverride).where(PhpIniOverride.account_id == account.id))
        current = _row_to_dict(row) if row else dict(DEFAULTS)
        account_id = account.id
        account_snapshot = account

    memory_limit = validate_php_memory_limit(params.get("memory_limit", current["memory_limit"]))
    upload_max_filesize = validate_php_size(params.get("upload_max_filesize", current["upload_max_filesize"]), "upload_max_filesize")
    post_max_size = validate_php_size(params.get("post_max_size", current["post_max_size"]), "post_max_size")
    max_execution_time = validate_php_max_execution_time(params.get("max_execution_time", current["max_execution_time"]))
    display_errors = bool(params.get("display_errors", current["display_errors"]))
    error_reporting = validate_php_error_reporting(params.get("error_reporting", current["error_reporting"]))

    # PHP itself refuses to accept an upload larger than post_max_size --
    # a conservative, explicit check here means a customer gets a clear
    # validation error from Forgehost instead of silently-broken uploads
    # discovered later.
    if _php_size_to_mb(post_max_size) < _php_size_to_mb(upload_max_filesize):
        raise ValidationError("post_max_size must be >= upload_max_filesize")

    with write_session() as session:
        row = session.scalar(select(PhpIniOverride).where(PhpIniOverride.account_id == account_id))
        if row is None:
            row = PhpIniOverride(account_id=account_id)
            session.add(row)
        row.memory_limit = memory_limit
        row.upload_max_filesize = upload_max_filesize
        row.post_max_size = post_max_size
        row.max_execution_time = max_execution_time
        row.display_errors = display_errors
        row.error_reporting = error_reporting
        row.updated_at = utcnow()
        session.flush()
        result = _row_to_dict(row)

    # Reload only this account's own vhost(s) -- ols.refresh_vhost()
    # regenerates exactly this account's domain-vhosts (plus the always-
    # fully-regenerated shared httpd_config.conf, ARCHITECTURE.md SS6/SS7),
    # going through the same validate->apply->reload->verify->rollback
    # pipeline every other config change in this project uses.
    ols.refresh_vhost(account_snapshot)
    # A vhost/web-server reload alone is not enough for a php_ini change
    # specifically -- see sysops.recycle_php_workers's own docstring for
    # why (confirmed live: a stale ini value can persist for well over a
    # minute otherwise, from already-warm pooled LSAPI workers).
    sysops.recycle_php_workers(username)

    return {"username": username, "php_ini": result}


def reset_php_ini(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        row = session.scalar(select(PhpIniOverride).where(PhpIniOverride.account_id == account.id))
        if row is not None:
            session.delete(row)
        account_snapshot = account

    ols.refresh_vhost(account_snapshot)
    sysops.recycle_php_workers(username)
    return {"username": username, "status": "reset_to_defaults"}


def terminate_account_php_ini(account: Account) -> None:
    """TERMINATE_HOOKS entry: drop this account's override row (no
    vhost exists to render it into anymore, and a later `reactivate`
    starting from clean PHP defaults is safer than silently reapplying
    stale settings the operator no longer has visibility into).
    Idempotent -- safe even if the account never had one."""
    with write_session() as session:
        row = session.scalar(select(PhpIniOverride).where(PhpIniOverride.account_id == account.id))
        if row is not None:
            session.delete(row)
