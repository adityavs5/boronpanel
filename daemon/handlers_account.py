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
from shared.models import Account, ApiToken, PanelUser, Session
from shared.validation import ValidationError, validate_password_strength, validate_php_version, validate_username

from daemon import cgroups, sysops

logger = logging.getLogger("borond.account")

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
        "plan_id": account.plan_id,
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

    # Validated before create_linux_user, not after: a rejected weak
    # custom password must never leave an orphaned Linux user behind (no
    # account DB row exists yet at this point either) -- a real ordering
    # bug this validation itself made newly reachable (a weak password
    # was previously always silently accepted, so this failure path had
    # never actually been exercised before), found live during Phase 4
    # feature 12's own verification and fixed here rather than left as a
    # known gap.
    password = validate_password_strength(params["password"]) if params.get("password") else None

    with write_session() as session:
        existing = session.scalar(select(Account).where(Account.username == username))
        if existing is not None:
            raise RuntimeError(f"account '{username}' already exists")

    uid, gid = sysops.create_linux_user(username)

    password = password or sysops.generate_password()
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

    # Phase 7b feature 3: the "account created" email needs the plaintext
    # initial password, which exists only in this function's own local
    # scope (never persisted -- PanelUser/Account never store it) and is
    # gone the moment this call returns. CREATE_HOOKS callables only ever
    # receive the Account row (every other hook -- cgroups, namespace --
    # only needs fields already on it), so a transient, non-mapped
    # attribute is stashed here rather than widening that shared signature
    # for every other hook consumer just for this one's sake.
    account_snapshot.initial_password = password
    for hook in CREATE_HOOKS:
        try:
            hook(account_snapshot)
        except Exception:
            logger.exception("create hook %s failed for %s", hook, username)

    result["initial_password"] = password
    return result


def reactivate_account(params: dict) -> dict:
    """Recreates a terminated account's Linux user + cgroup slice and
    flips its existing DB row back to active, instead of inserting a new
    row. Used by daemon/backup.py's full-restore path when restoring onto
    a terminated account: a plain create_account() call there would
    always hit its own "account already exists" guard, since terminating
    an account never deletes its historical row (a deliberate pattern --
    see terminate_account) -- found live when a restore silently "succeeded"
    past that guard (treated as a harmless already-exists no-op) without
    ever actually recreating the Linux user, then failed later trying to
    chown restored files to a uid that no longer existed."""
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found -- use account.create for a genuinely new account")
        if account.status not in ("terminated", "error"):
            raise RuntimeError(f"account '{username}' is '{account.status}', not terminated -- nothing to reactivate")

    php_version = validate_php_version(params.get("php_version") or settings.default_php_version, settings.php_versions)
    quota_soft_mb = int(params.get("quota_soft_mb") or settings.default_quota_soft_mb)
    quota_hard_mb = int(params.get("quota_hard_mb") or settings.default_quota_hard_mb)
    if quota_hard_mb < quota_soft_mb:
        raise ValidationError("quota_hard_mb must be >= quota_soft_mb")
    cpu_pct = int(params.get("cpu_pct") or cgroups.DEFAULT_CPU_PCT)
    mem_mb = int(params.get("mem_mb") or cgroups.DEFAULT_MEM_MB)
    io_mb = int(params.get("io_mb") or cgroups.DEFAULT_IO_MB)
    pids_max = int(params.get("pids_max") or cgroups.DEFAULT_PIDS_MAX)
    _validate_limits(cpu_pct, mem_mb, io_mb, pids_max)

    # Validated before create_linux_user -- see create_account's identical
    # ordering fix and its own comment for why.
    password = validate_password_strength(params["password"]) if params.get("password") else None

    uid, gid = sysops.create_linux_user(username)
    password = password or sysops.generate_password()
    sysops.set_initial_password(username, password)
    try:
        sysops.set_quota(username, quota_soft_mb, quota_hard_mb)
    except Exception:
        logger.exception("quota setup failed for %s during reactivation (continuing)", username)

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        account.status = "active"
        account.uid = uid
        account.gid = gid
        account.php_version = php_version
        account.quota_soft_mb = quota_soft_mb
        account.quota_hard_mb = quota_hard_mb
        account.cpu_pct = cpu_pct
        account.mem_mb = mem_mb
        account.io_mb = io_mb
        account.pids_max = pids_max
        account.terminated_at = None
        account.suspended_at = None
        account.last_error = None

        # Symmetric with terminate_account's F3 fix: reactivation is an
        # explicit, admin-invoked action on this same account_id (not a
        # new account), so the original customer's panel login(s) should
        # come back with it -- otherwise every reactivated account would
        # need a brand new PanelUser created by hand even when it's
        # genuinely the same customer resuming (e.g. the backup-restore
        # "terminate -> restore" flow this function also serves).
        panel_users = session.scalars(select(PanelUser).where(PanelUser.account_id == account.id)).all()
        for pu in panel_users:
            pu.disabled = False

        session.flush()
        result = _account_to_dict(account)
        account_snapshot = account

    account_snapshot.initial_password = password
    for hook in CREATE_HOOKS:
        try:
            hook(account_snapshot)
        except Exception:
            logger.exception("create hook %s failed for reactivated account %s", hook, username)

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

        # Security audit finding F3: every *system* resource above is torn
        # down by TERMINATE_HOOKS, but nothing ever touched this account's
        # panel login(s) -- a terminated customer kept a fully valid
        # session/token indefinitely, and if the username were ever
        # reactivated or repurposed for a different customer, the old
        # credentials would silently regain real access (require_account_access
        # only checks account.username, not who the credentials originally
        # belonged to). Disable every PanelUser row scoped to this account,
        # revoke their active sessions, and revoke their API tokens --
        # applied even on a partial/"error" termination, since a resource
        # that failed to tear down is exactly when access should be locked
        # down hardest, not left open.
        panel_users = session.scalars(select(PanelUser).where(PanelUser.account_id == account.id)).all()
        panel_user_ids = [pu.id for pu in panel_users]
        for pu in panel_users:
            pu.disabled = True
        if panel_user_ids:
            active_sessions = session.scalars(
                select(Session).where(Session.panel_user_id.in_(panel_user_ids), Session.revoked == False)  # noqa: E712
            ).all()
            for row in active_sessions:
                row.revoked = True
        tokens = session.scalars(
            select(ApiToken).where(ApiToken.account_id == account.id, ApiToken.revoked_at.is_(None))
        ).all()
        for token in tokens:
            token.revoked_at = utcnow()

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
