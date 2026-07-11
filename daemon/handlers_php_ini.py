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
from shared.models import Account, PhpIniDirective, PhpIniOverride, utcnow
from shared.validation import (
    ValidationError,
    _php_size_to_mb,
    validate_php_error_reporting,
    validate_php_max_execution_time,
    validate_php_memory_limit,
    validate_php_size,
    validate_username,
)

from daemon import ols, phpdirectives, sysops

# Re-exported name: the DEFAULTS dict moved to daemon/phpdirectives.py (ols.py
# needs it too and importing this module from there would be circular), but
# every existing caller/test referencing handlers_php_ini.DEFAULTS still works.
DEFAULTS = phpdirectives.DEFAULTS

LEGACY_FIELDS = ("memory_limit", "upload_max_filesize", "post_max_size",
                 "max_execution_time", "display_errors", "error_reporting")


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


def _extras_for_account(session, account_id: int) -> dict[str, str]:
    rows = session.scalars(select(PhpIniDirective).where(PhpIniDirective.account_id == account_id)).all()
    return {r.name: r.value for r in rows}


def _directive_descriptors(legacy: dict | None, extras: dict[str, str]) -> list[dict]:
    """One uniform descriptor per supported directive (legacy six + extras),
    so the UI renders every field -- type, bounds, default, current
    override -- from this single server-provided list instead of hardcoding
    a second copy of the registry in JavaScript."""
    result = []
    for spec in phpdirectives.LEGACY_DIRECTIVES:
        name = spec["name"]
        result.append({
            **spec,
            "default": DEFAULTS[name],
            "value": legacy[name] if legacy else None,
        })
    for name, spec in phpdirectives.EXTRA_DIRECTIVES.items():
        result.append({
            "name": name,
            "type": spec["type"],
            "min": spec.get("min"),
            "max": spec.get("max"),
            "default": spec["default"],
            "value": extras.get(name),
        })
    return result


def get_php_ini(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        row = session.scalar(select(PhpIniOverride).where(PhpIniOverride.account_id == account.id))
        extras = _extras_for_account(session, account.id)
        legacy = _row_to_dict(row) if row else None
        return {
            "username": username,
            "php_ini": legacy,
            "defaults": DEFAULTS,
            "extras": extras,
            "directives": _directive_descriptors(legacy, extras),
        }


def set_php_ini(params: dict) -> dict:
    username = validate_username(params["username"])

    # The `directives` dict is how the API transports every directive
    # uniformly (names like "session.gc_maxlifetime" can't travel as RPC
    # kwargs) -- legacy six included. Fold any legacy names it carries into
    # the flat params, leaving only EXTRA_DIRECTIVES entries behind.
    directives = dict(params.get("directives") or {})
    for field in LEGACY_FIELDS:
        if field in directives:
            params.setdefault(field, directives.pop(field))
    for name in directives:
        if name not in phpdirectives.EXTRA_DIRECTIVES:
            raise ValidationError(f"'{name}' is not a supported PHP directive")

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

    # Only touch the legacy PhpIniOverride row when a legacy field was
    # actually sent -- a PATCH that only flips max_input_vars must not
    # materialize a six-column override row as a side effect.
    touch_legacy = any(field in params for field in LEGACY_FIELDS)

    memory_limit = validate_php_memory_limit(params.get("memory_limit", current["memory_limit"]))
    upload_max_filesize = validate_php_size(params.get("upload_max_filesize", current["upload_max_filesize"]), "upload_max_filesize")
    post_max_size = validate_php_size(params.get("post_max_size", current["post_max_size"]), "post_max_size")
    max_execution_time = validate_php_max_execution_time(params.get("max_execution_time", current["max_execution_time"]))
    display_errors = bool(params.get("display_errors", current["display_errors"]))
    error_reporting = validate_php_error_reporting(params.get("error_reporting", current["error_reporting"]))

    # PHP itself refuses to accept an upload larger than post_max_size --
    # a conservative, explicit check here means a customer gets a clear
    # validation error from Boron instead of silently-broken uploads
    # discovered later.
    if _php_size_to_mb(post_max_size) < _php_size_to_mb(upload_max_filesize):
        raise ValidationError("post_max_size must be >= upload_max_filesize")

    # None means "revert this directive to its default" (drop the row);
    # anything else is validated to its rendered string form first.
    validated_extras = {
        name: (None if value is None else phpdirectives.validate(name, value))
        for name, value in directives.items()
    }

    with write_session() as session:
        if touch_legacy:
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
        for name, value in validated_extras.items():
            existing = session.scalar(select(PhpIniDirective).where(
                PhpIniDirective.account_id == account_id, PhpIniDirective.name == name))
            if value is None:
                if existing is not None:
                    session.delete(existing)
            elif existing is None:
                session.add(PhpIniDirective(account_id=account_id, name=name, value=value))
            else:
                existing.value = value
                existing.updated_at = utcnow()
        session.flush()
        row = session.scalar(select(PhpIniOverride).where(PhpIniOverride.account_id == account_id))
        result = _row_to_dict(row) if row else None
        extras = _extras_for_account(session, account_id)

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

    return {
        "username": username,
        "php_ini": result,
        "extras": extras,
        "directives": _directive_descriptors(result, extras),
    }


def reset_php_ini(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        row = session.scalar(select(PhpIniOverride).where(PhpIniOverride.account_id == account.id))
        if row is not None:
            session.delete(row)
        for extra in session.scalars(select(PhpIniDirective).where(PhpIniDirective.account_id == account.id)).all():
            session.delete(extra)
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
        for extra in session.scalars(select(PhpIniDirective).where(PhpIniDirective.account_id == account.id)).all():
            session.delete(extra)
