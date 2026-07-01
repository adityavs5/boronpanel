"""Account CRUD operations: Phase a (this module) plus extension points that
later phases (b: vhost, c: DNS, d: databases, e: mail) hook into for
suspend/terminate, per ARCHITECTURE.md SS5's fixed teardown order.

Each phase that adds account-scoped resources registers a teardown callable
in TERMINATE_HOOKS and a suspend/unsuspend callable in
SUSPEND_HOOKS/UNSUSPEND_HOOKS, so terminate_account's ordering stays defined
in one place instead of being rewritten per phase.
"""
from __future__ import annotations

import logging
from typing import Callable

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account
from shared.validation import ValidationError, validate_php_version, validate_username

from daemon import cgroups, sysops

logger = logging.getLogger("forgehostd.account")

# Populated by later phases (vhost teardown in Phase b, DB in Phase d, mail in
# Phase e, DNS in Phase c). Each hook receives the Account row and must be
# safe to call even if that phase's resources were never provisioned for this
# account (idempotent no-op in that case).
TERMINATE_HOOKS: list[Callable[[Account], None]] = []
SUSPEND_HOOKS: list[Callable[[Account], None]] = []
UNSUSPEND_HOOKS: list[Callable[[Account], None]] = []
# Phase 2 feature 1: re-render/reload just this account's vhost after its
# php_version column changes, same wiring pattern as the hooks above.
PHP_VERSION_HOOKS: list[Callable[[Account], None]] = []
# Phase 2 feature 6: create the account's cgroup slice with its (default or
# requested) resource limits right after the Linux user/DB row exist.
CREATE_HOOKS: list[Callable[[Account], None]] = []
# Re-applies cgroup limits after set_limits() changes them.
LIMITS_HOOKS: list[Callable[[Account], None]] = []


def _account_to_dict(account: Account) -> dict:
    return {
        "id": account.id,
        "username": account.username,
        "status": account.status,
        "uid": account.uid,
        "gid": account.gid,
        "primary_domain": account.primary_domain,
        "php_version": account.php_version,
        "quota_soft_mb": account.quota_soft_mb,
        "quota_hard_mb": account.quota_hard_mb,
        "cpu_pct": account.cpu_pct,
        "mem_mb": account.mem_mb,
        "io_mb": account.io_mb,
        "pids_max": account.pids_max,
        "last_error": account.last_error,
        "created_at": account.created_at.isoformat() if account.created_at else None,
        "suspended_at": account.suspended_at.isoformat() if account.suspended_at else None,
        "terminated_at": account.terminated_at.isoformat() if account.terminated_at else None,
    }


def _validate_limits(cpu_pct: int, mem_mb: int, io_mb: int, pids_max: int) -> None:
    if not (1 <= cpu_pct <= 100):
        raise ValidationError("cpu_pct must be between 1 and 100")
    if not (64 <= mem_mb <= 65536):
        raise ValidationError("mem_mb must be between 64 and 65536")
    if not (1 <= io_mb <= 10000):
        raise ValidationError("io_mb must be between 1 and 10000")
    if not (10 <= pids_max <= 10000):
        raise ValidationError("pids_max must be between 10 and 10000")


def create_account(params: dict) -> dict:
    username = validate_username(params["username"])
    php_version = validate_php_version(
        params.get("php_version", settings.default_php_version), settings.php_versions
    )
    quota_soft_mb = int(params.get("quota_soft_mb", settings.default_quota_soft_mb))
    quota_hard_mb = int(params.get("quota_hard_mb", settings.default_quota_hard_mb))
    if quota_hard_mb < quota_soft_mb:
        raise ValidationError("quota_hard_mb must be >= quota_soft_mb")
    primary_domain = params.get("primary_domain")

    cpu_pct = int(params.get("cpu_pct", cgroups.DEFAULT_CPU_PCT))
    mem_mb = int(params.get("mem_mb", cgroups.DEFAULT_MEM_MB))
    io_mb = int(params.get("io_mb", cgroups.DEFAULT_IO_MB))
    pids_max = int(params.get("pids_max", cgroups.DEFAULT_PIDS_MAX))
    _validate_limits(cpu_pct, mem_mb, io_mb, pids_max)

    with write_session() as session:
        existing = session.scalar(select(Account).where(Account.username == username))
        if existing is not None:
            raise RuntimeError(f"account '{username}' already exists")

    uid, gid = sysops.create_linux_user(username)

    password = params.get("password") or sysops.generate_password()
    sysops.set_initial_password(username, password)

    try:
        sysops.set_quota(username, quota_soft_mb, quota_hard_mb)
    except Exception:
        logger.exception("quota setup failed for %s (continuing - account usable without enforced quota)", username)

    with write_session() as session:
        account = Account(
            username=username,
            status="active",
            uid=uid,
            gid=gid,
            primary_domain=primary_domain,
            php_version=php_version,
            quota_soft_mb=quota_soft_mb,
            quota_hard_mb=quota_hard_mb,
            cpu_pct=cpu_pct,
            mem_mb=mem_mb,
            io_mb=io_mb,
            pids_max=pids_max,
        )
        session.add(account)
        session.flush()
        result = _account_to_dict(account)
        account_snapshot = account

    for hook in CREATE_HOOKS:
        try:
            hook(account_snapshot)
        except Exception:
            logger.exception("create hook %s failed for %s", hook, username)

    result["initial_password"] = password
    return result


def set_limits(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        if account.status not in ("active", "suspended"):
            raise RuntimeError(f"cannot change limits for an account in status '{account.status}'")

        cpu_pct = int(params.get("cpu_pct", account.cpu_pct))
        mem_mb = int(params.get("mem_mb", account.mem_mb))
        io_mb = int(params.get("io_mb", account.io_mb))
        pids_max = int(params.get("pids_max", account.pids_max))
        _validate_limits(cpu_pct, mem_mb, io_mb, pids_max)

        account.cpu_pct = cpu_pct
        account.mem_mb = mem_mb
        account.io_mb = io_mb
        account.pids_max = pids_max
        session.flush()
        result = _account_to_dict(account)
        account_snapshot = account

    for hook in LIMITS_HOOKS:
        hook(account_snapshot)

    return result


def get_account(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        return _account_to_dict(account)


def list_accounts(params: dict) -> dict:
    with write_session() as session:
        accounts = session.scalars(select(Account).order_by(Account.username)).all()
        return {"accounts": [_account_to_dict(a) for a in accounts]}


def suspend_account(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        if account.status == "suspended":
            return _account_to_dict(account)
        if account.status != "active":
            raise RuntimeError(f"cannot suspend account in status '{account.status}'")

        sysops.lock_user(username)
        for hook in SUSPEND_HOOKS:
            hook(account)

        from shared.models import utcnow

        account.status = "suspended"
        account.suspended_at = utcnow()
        session.flush()
        return _account_to_dict(account)


def unsuspend_account(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        if account.status == "active":
            return _account_to_dict(account)
        if account.status != "suspended":
            raise RuntimeError(f"cannot unsuspend account in status '{account.status}'")

        sysops.unlock_user(username)
        for hook in UNSUSPEND_HOOKS:
            hook(account)

        account.status = "active"
        account.suspended_at = None
        session.flush()
        return _account_to_dict(account)


def terminate_account(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        if account.status == "terminated":
            return _account_to_dict(account)
        account.status = "terminating"
        session.flush()
        account_snapshot = _account_to_dict(account)

    errors: list[str] = []
    for hook in TERMINATE_HOOKS:
        try:
            hook(account)  # account is detached but read-only fields (username/id) are safe to use
        except Exception as exc:  # noqa: BLE001 - one hook failing must not block the rest
            logger.exception("terminate hook %s failed for %s", hook, username)
            errors.append(f"{hook.__module__}.{hook.__qualname__}: {exc}")

    try:
        sysops.remove_quota(username)
        sysops.delete_linux_user(username)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"linux user teardown: {exc}")

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        from shared.models import utcnow

        if errors:
            account.status = "error"
            account.last_error = "; ".join(errors)
        else:
            account.status = "terminated"
            account.terminated_at = utcnow()
        session.flush()
        return _account_to_dict(account)


def set_php_version(params: dict) -> dict:
    """Phase 2 feature 1: switch one account's PHP version. Re-renders and
    reloads only that account's own vhconf.conf (+ the shared
    httpd_config.conf, which the existing ConfigWriterMulti pipeline always
    regenerates in full -- ARCHITECTURE.md SS6/SS7) -- no other account's
    vhost content changes, since each account's extprocessor/vhost block is
    rendered independently from its own Account.php_version column."""
    username = validate_username(params["username"])
    new_version = validate_php_version(params["php_version"], settings.php_versions)

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        if account.status not in ("active", "suspended"):
            raise RuntimeError(f"cannot change PHP version for an account in status '{account.status}'")
        if account.php_version == new_version:
            return _account_to_dict(account)

        account.php_version = new_version
        session.flush()
        account_snapshot = account

    for hook in PHP_VERSION_HOOKS:
        hook(account_snapshot)

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        return _account_to_dict(account)
