"""Account CRUD operations: Phase a (this module) plus extension points that
later phases (b: vhost, c: DNS, d: databases, e: mail) hook into for
suspend/terminate, per ARCHITECTURE.md SS5's fixed teardown order.

Each phase that adds account-scoped resources registers a teardown callable
in TERMINATE_HOOKS and a suspend/unsuspend callable in
SUSPEND_HOOKS/UNSUSPEND_HOOKS, so terminate_account's ordering stays defined
in one place instead of being rewritten per phase.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Callable

from daemon import account_mutation, audit, database_operations, dns_operations, impersonation

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, AccountNotificationPrefs, ApiToken, Domain, PanelUser, Session, utcnow
from shared.passwords import hash_password
from shared.validation import (
    ConflictError,
    NotFoundError,
    ValidationError,
    validate_domain,
    validate_email_address,
    validate_password_strength,
    validate_php_version,
    validate_username,
)

from daemon import cgroups, handlers_domain, inode_quotas, license_client, notifications, ols, sysops

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
# Create the account's aggregate user slice with its resource limits right
# after the Linux user/DB row exist, before customer sessions are admitted.
CREATE_HOOKS: list[Callable[[Account], None]] = []
# Re-applies cgroup limits after set_limits() changes them.
LIMITS_HOOKS: list[Callable[[Account], None]] = []


def _effective_limits(account: Account) -> dict:
    """The CPU/memory/IO/process limits the account's slice actually enforces.

    The legacy Account columns are only the bottom layer: a server, reseller
    plan, hosting plan or per-account policy row replaces them completely
    (resource_manager.effective_for_account). Reporting the columns made the
    account page show -- and its limits form re-submit -- values that were
    never applied.
    """
    from sqlalchemy.orm import object_session
    from daemon.resource_manager import available_cpu_cores, effective_for_account

    session = object_session(account)
    if session is None or account.id is None:
        configured = account.cpu_pct / 100
        return {
            "cpu_pct": account.cpu_pct,
            "cpu_cores": min(configured, available_cpu_cores()),
            "configured_cpu_cores": configured,
            "mem_mb": account.mem_mb,
            "io_mb": account.io_mb,
            "pids_max": account.pids_max,
            "limits_source": "account",
            "resource_override": False,
            "resource_policy": None,
        }
    effective = effective_for_account(session, account)
    values = effective["values"]
    configured = effective["configured_cpu_cores"]
    read_bps = values.get("io_read_bps")
    policy = effective["policy"]
    return {
        "cpu_pct": None if configured is None else round(configured * 100),
        "cpu_cores": values.get("cpu_cores"),
        "configured_cpu_cores": configured,
        "mem_mb": values.get("memory_max_mb"),
        "io_mb": None if read_bps is None else max(1, round(read_bps / 1048576)),
        "pids_max": values.get("nproc"),
        # "account" means the legacy columns (or an account override) win;
        # otherwise the scope whose policy row is enforced.
        "limits_source": policy["scope_type"] if policy else "account",
        # An account-scope resource policy (admin.resources account override)
        # wins over every other scope; "account" above cannot tell it apart
        # from the legacy columns.
        "resource_override": bool(policy and policy["scope_type"] == "account"),
        "resource_policy": values,
    }


def _account_to_dict(account: Account) -> dict:
    from daemon.resource_manager import available_cpu_cores, host_memory_gb
    from daemon.inode_quotas import enforcement_state
    limits = _effective_limits(account)
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
        "inode_soft_limit": account.inode_soft_limit,
        "inode_hard_limit": account.inode_hard_limit,
        "inode_limit": account.inode_hard_limit or None,
        "inode_enforce_at": account.inode_enforce_at.isoformat() if account.inode_enforce_at else None,
        "inode_enforcement_state": enforcement_state(account),
        "php_private_sessions": account.php_private_sessions,
        **limits,
        "host_cpu_cores": available_cpu_cores(),
        "host_memory_gb": host_memory_gb(),
        "plan_id": account.plan_id,
        "last_error": account.last_error,
        "created_at": account.created_at.isoformat() if account.created_at else None,
        "suspended_at": account.suspended_at.isoformat() if account.suspended_at else None,
        "terminated_at": account.terminated_at.isoformat() if account.terminated_at else None,
    }


def _validate_limits(cpu_pct: int, mem_mb: int, io_mb: int, pids_max: int) -> None:
    # systemd defines 100% as one complete CPU core and permits values over
    # 100%. Boron's storage remains percentage-based for upgrade/backup
    # compatibility, while the API/UI expose the easier-to-understand core
    # value (cpu_pct / 100).
    from daemon.resource_manager import available_cpu_cores
    maximum = available_cpu_cores() * 100
    if not (1 <= cpu_pct <= maximum):
        raise ValidationError(
            f"cpu limit must be between 0.01 and {available_cpu_cores():g} cores on this server"
        )
    if not (64 <= mem_mb <= 65536):
        raise ValidationError("Memory must be between 0.0625 and 64 GB")
    if not (1 <= io_mb <= 10000):
        raise ValidationError("Disk I/O throughput must be between 1 and 10000 MB/s")
    if not (10 <= pids_max <= 10000):
        raise ValidationError("Maximum processes must be between 10 and 10000")


def _home_exists(username: str) -> bool:
    return os.path.lexists(f"{settings.home_base}/{username}")


def _rollback_new_linux_user(username: str, *, home_existed: bool) -> None:
    """Undo a Linux identity this request created.

    A home that useradd created goes with it: left behind it stays owned by
    the released uid, which useradd hands to the next account it creates. A
    home that existed before (an earlier tenant's or restored data) is kept.
    """
    try:
        sysops.lock_user(username)
    except Exception:
        logger.exception("Could not lock newly provisioned identity %s during rollback", username)
    try:
        sysops.delete_linux_user(username, remove_home=not home_existed)
    except Exception:
        logger.exception("New identity %s requires administrator cleanup after provisioning failure", username)


def _set_identity(username: str, uid: int | None, gid: int | None) -> None:
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        account.uid, account.gid = uid, gid


def ensure_customer_login(session, account, password: str) -> PanelUser:
    """Provision the hosting login in the same root-owned transaction.

    The initial password is a panel credential as well as the Linux login.
    A colliding administrator or another account's login is never adopted.
    Explicit reactivation/reset invalidates previous sessions and challenges.
    """
    from shared.models import LoginChallenge

    user = session.scalar(select(PanelUser).where(PanelUser.username == account.username))
    if user is not None and (user.role != "customer" or user.account_id != account.id):
        raise ValidationError("The panel login name is already used by another account")
    if user is None:
        user = PanelUser(username=account.username, role="customer", account_id=account.id,
                         password_hash=hash_password(password))
        session.add(user)
        session.flush()
    else:
        user.password_hash = hash_password(password)
        for row in session.scalars(select(Session).where(Session.panel_user_id == user.id)).all():
            row.revoked = True
        for row in session.scalars(select(LoginChallenge).where(LoginChallenge.panel_user_id == user.id)).all():
            session.delete(row)
    user.disabled = False
    return user


TOMBSTONE_SEPARATOR = "~"


def release_terminated_username(session, account: Account) -> str:
    """Free a terminated account's name for a new account; returns the tombstone.

    Terminated rows are kept: backups, snapshots, tasks, tokens and sessions
    reference their id, and the account-event log is keyed by name. Reusing
    the row for a different customer would hand those to the new owner, so
    the old row and its disabled panel logins are renamed to
    "<name>~<id>" instead. "~" can never pass validate_username, so no
    request can address a tombstone by name, while the original name stays
    readable in every historical listing. The new account gets a new id and
    therefore none of the old one's history.
    """
    from shared.models import LoginAttempt

    if account.status != "terminated":
        raise ConflictError(f"account '{account.username}' is not terminated")
    original = account.username
    suffix = f"{TOMBSTONE_SEPARATOR}{account.id}"
    for user in session.scalars(select(PanelUser).where(PanelUser.account_id == account.id)).all():
        user.disabled = True
        if not user.username.endswith(suffix):
            user.username = f"{user.username}{suffix}"
    account.username = f"{original}{suffix}"
    # Lockout counters belong to the old login, not to the next customer.
    attempt = session.scalar(select(LoginAttempt).where(LoginAttempt.username == original))
    if attempt is not None:
        session.delete(attempt)
    session.flush()
    logger.info("released terminated account name %s (kept as %s)", original, account.username)
    return account.username


def plan_seed(plan_id) -> dict:
    """Initial limits from a requested hosting plan; refuses an unknown plan.

    CPU, memory, I/O and process values are the ones the plan's policy
    enforces, not the plan's own legacy columns, which plans written before
    3.3.2 let drift from it (cpu_pct 25 next to cpu_cores 1.0 started the
    account on a quarter core).
    """
    if plan_id is None:
        return {}
    from daemon import plans
    from shared.models import Plan
    try:
        plan_id = int(plan_id)
    except (TypeError, ValueError):
        raise ValidationError("plan_id must be an integer") from None
    with write_session() as session:
        plan = session.get(Plan, plan_id)
        if plan is None:
            raise NotFoundError(f"plan {plan_id} not found")
        return plans.account_seed(session, plan)


@database_operations.serialized
def create_account(params: dict) -> dict:
    username = validate_username(params["username"])
    # A requested plan is checked before anything exists: a typo in plan_id
    # used to leave a fully provisioned account on default limits. The plan
    # also seeds the initial limits, so the account never runs on defaults;
    # the caller still applies the plan (usage limits, Redis, plan_id).
    seed = plan_seed(params.get("plan_id"))
    params = {**seed, **{key: value for key, value in params.items() if value is not None or key not in seed}}
    php_version = validate_php_version(
        params.get("php_version", settings.default_php_version), settings.php_versions
    )
    quota_soft_mb = int(params.get("quota_soft_mb", settings.default_quota_soft_mb))
    quota_hard_mb = int(params.get("quota_hard_mb", settings.default_quota_hard_mb))
    if quota_hard_mb < quota_soft_mb:
        raise ValidationError("quota_hard_mb must be >= quota_soft_mb")
    raw_primary_domain = params.get("primary_domain")
    primary_domain = validate_domain(raw_primary_domain) if raw_primary_domain is not None else None

    cpu_pct = int(params.get("cpu_pct", cgroups.DEFAULT_CPU_PCT))
    mem_mb = int(params.get("mem_mb", cgroups.DEFAULT_MEM_MB))
    io_mb = int(params.get("io_mb", cgroups.DEFAULT_IO_MB))
    pids_max = int(params.get("pids_max", cgroups.DEFAULT_PIDS_MAX))
    _validate_limits(cpu_pct, mem_mb, io_mb, pids_max)
    inode_soft, inode_hard = inode_quotas.validate_limits(
        params.get("inode_soft_limit", inode_quotas.DEFAULT_SOFT),
        params.get("inode_hard_limit", inode_quotas.DEFAULT_HARD),
    )
    # An empty home has nothing to warn about: a new account's inode limits
    # are due at once and applied below. (They used to start a 7-day warning
    # period, so every new account reported "warning" and ran without an
    # inode limit for a week.) Archive importers stage them behind that
    # warning period instead: the imported home may already hold more files
    # than the limits allow.
    staged_inodes = bool(params.get("stage_inode_limits"))
    inode_enforce_at = None if not inode_hard else (
        utcnow() + dt.timedelta(days=inode_quotas.DEFAULT_WARNING_DAYS) if staged_inodes else utcnow()
    )

    # Validated before create_linux_user, not after: a rejected weak
    # custom password must never leave an orphaned Linux user behind (no
    # account DB row exists yet at this point either) -- a real ordering
    # bug this validation itself made newly reachable (a weak password
    # was previously always silently accepted, so this failure path had
    # never actually been exercised before), found live during Phase 4
    # feature 12's own verification and fixed here rather than left as a
    # known gap.
    password = validate_password_strength(params["password"]) if params.get("password") else None
    # QA round 2, item 15: an admin-supplied contact email at creation time
    # -- validated up front for the same reason the password is (a rejected
    # bad email must never leave an orphaned Linux user behind).
    email = validate_email_address(params["email"]) if params.get("email") else None
    # Checked before any system change, like the validations above.
    license_client.require_account_capacity()

    with write_session() as session:
        existing = session.scalar(select(Account).where(Account.username == username))
        if existing is not None:
            if existing.status != "terminated":
                if existing.status == "error":
                    raise ConflictError(
                        f"account '{username}' did not finish terminating; retry termination before reusing the name"
                    )
                raise ConflictError(f"account '{username}' already exists")
            release_terminated_username(session, existing)
        if session.scalar(select(PanelUser.id).where(PanelUser.username == username)) is not None:
            raise ValidationError("The panel login name is already used; choose another username")
        if primary_domain is not None:
            from daemon import domain_ownership
            domain_ownership.require_available(session, primary_domain, None)
            existing_domain = session.scalar(select(Domain).where(Domain.domain == primary_domain))
            if existing_domain is not None:
                raise ConflictError(f"domain '{primary_domain}' is already in use")

    home_existed = _home_exists(username)
    uid, gid = sysops.create_linux_user(username)

    password = password or sysops.generate_password()
    try:
        # Mandatory resource enforcement precedes credentials and active state.
        cgroups.apply_limits(username, cpu_pct, mem_mb, io_mb, pids_max, uid=uid)
        sysops.set_initial_password(username, password)
        sysops.set_quota(username, quota_soft_mb, quota_hard_mb)
    except Exception:
        # No hosting resource or credential row has been created yet. Do not
        # activate an account whose mandatory identity/quota setup failed.
        _rollback_new_linux_user(username, home_existed=home_existed)
        raise

    try:
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
                php_private_sessions=True,
                inode_soft_limit=inode_soft,
                inode_hard_limit=inode_hard,
                inode_enforce_at=inode_enforce_at,
            )
            session.add(account)
            session.flush()
            ensure_customer_login(session, account, password)
            primary_domain_id = None
            if primary_domain is not None:
                primary = Domain(
                    account_id=account.id,
                    domain=primary_domain,
                    kind="primary",
                    docroot=f"{settings.home_base}/{username}/public_html",
                )
                session.add(primary)
                session.flush()
                primary_domain_id = primary.id
            if email:
                # Same table the customer/admin "notification preferences" page
                # edits later (daemon/notifications.py) -- setting it here at
                # creation time both records the contact email AND makes the
                # "account created" welcome email (CREATE_HOOKS below) able to
                # actually reach someone, without a second, duplicate email
                # column on Account itself.
                prefs = notifications._get_prefs(session, account.id)
                prefs.customer_email = email
            result = _account_to_dict(account)
            result["email"] = email
            account_snapshot = account
    except Exception:
        _rollback_new_linux_user(username, home_existed=home_existed)
        raise

    # A failure is logged and left due; the periodic reconciler retries it.
    # The account is committed by now, so nothing here may stop the primary
    # domain below from being provisioned.
    try:
        if inode_hard and not staged_inodes and inode_quotas.enforce_due({"username": username})["applied"]:
            result.update(inode_enforce_at=None, inode_enforcement_state="enforced")
    except Exception:  # noqa: BLE001 - left due for the periodic reconciler
        logger.exception("could not apply the inode limits of new account %s", username)

    # A primary domain is part of account creation, not a display-only field:
    # its row is needed by ownership checks and by OLS's full vhost render.
    # The account/domain rows are committed before provisioning for the same
    # reason as handlers_domain.add_domain; compensate both values if the
    # docroot or OLS setup fails.
    dns_zone_created = False
    if primary_domain is not None:
        try:
            handlers_domain.ensure_docroot(
                username,
                f"{settings.home_base}/{username}/public_html",
                primary_domain,
                create_welcome=True,
            )
            # OLS opens per-vhost logs as its shared worker identity. Create
            # the private account log directory and its narrow ACL before
            # validating the first vhost, otherwise a new primary domain can
            # fail openlitespeed -t before the later migration helper runs.
            sysops.ensure_web_logs(username)
            ols.provision_vhost(account_snapshot)
            # Normal DirectAdmin/cPanel-style account creation provisions an
            # authoritative zone with the primary website. Archive importers
            # opt out because they restore the source zone immediately after
            # the account exists and must not collide with a generated zone.
            if params.get("create_dns_zone", True):
                handlers_domain._create_managed_zone(username, primary_domain)
                dns_zone_created = True
        except Exception:
            if dns_zone_created:
                try:
                    handlers_domain._delete_managed_zone(primary_domain)
                except Exception:  # noqa: BLE001 - preserve the provisioning failure
                    logger.exception("Could not remove DNS zone while rolling back account %s", username)
            with write_session() as session:
                primary = session.get(Domain, primary_domain_id)
                if primary is not None:
                    session.delete(primary)
                account = session.get(Account, account_snapshot.id)
                if account is not None:
                    account.primary_domain = None
            raise

    result["dns_zone_created"] = dns_zone_created

    for hook in CREATE_HOOKS:
        try:
            hook(account_snapshot)
        except Exception:
            logger.exception("create hook %s failed for %s", hook, username)

    result["initial_password"] = password
    return result


@account_mutation.locked
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
            raise NotFoundError(f"account '{username}' not found -- use account.create for a genuinely new account")
        if account.status not in ("terminated", "error"):
            raise ConflictError(f"account '{username}' is '{account.status}', not terminated -- nothing to reactivate")
        previous_identity = (account.uid, account.gid)

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

    home_existed = _home_exists(username)
    uid, gid = sysops.create_linux_user(username)
    password = password or sysops.generate_password()
    try:
        # The row names the new identity before the slice is set up: the
        # cgroup resolver cross-checks it against the Linux user, and the uid
        # a terminated row kept belongs to an identity that no longer exists
        # (useradd usually picks another uid), so direct reactivation failed.
        _set_identity(username, uid, gid)
        # Mandatory resource enforcement precedes credentials and active state.
        cgroups.apply_limits(username, cpu_pct, mem_mb, io_mb, pids_max, uid=uid)
        sysops.set_initial_password(username, password)
        sysops.set_quota(username, quota_soft_mb, quota_hard_mb)
    except Exception:
        # No hosting resource or credential row has been created yet. Do not
        # activate an account whose mandatory identity/quota setup failed.
        _rollback_new_linux_user(username, home_existed=home_existed)
        try:
            _set_identity(username, *previous_identity)
        except Exception:  # noqa: BLE001 - keep the setup failure as the error
            logger.exception("could not restore the previous identity of %s after a failed reactivation", username)
        raise

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
        # Reactivated accounts keep their historical session and inode policy.
        # The new uid's filesystem quota starts without the inode limits, and
        # the restored home may hold more files than they allow, so they go
        # through the warning period again instead of staying "enforced"
        # while nothing enforced them.
        if account.inode_hard_limit:
            account.inode_enforce_at = utcnow() + dt.timedelta(days=inode_quotas.DEFAULT_WARNING_DAYS)
        account.terminated_at = None
        account.suspended_at = None
        account.last_error = None
        # Termination released the domain rows (another account may own the
        # name by now); primary_domain was only their historical label. The
        # restore's domain.add of the primary sets it again.
        if account.primary_domain and session.scalar(select(Domain.id).where(
            Domain.account_id == account.id, Domain.domain == account.primary_domain,
        )) is None:
            account.primary_domain = None

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
        ensure_customer_login(session, account, password)

        session.flush()
        result = _account_to_dict(account)
        account_snapshot = account

    for hook in CREATE_HOOKS:
        try:
            hook(account_snapshot)
        except Exception:
            logger.exception("create hook %s failed for reactivated account %s", hook, username)

    result["initial_password"] = password
    return result


LIMIT_FIELDS = ("cpu_pct", "mem_mb", "io_mb", "pids_max")


def _write_limits_override(session, account: Account, changed: dict) -> None:
    """Make an explicit per-account limit edit the enforced contract.

    Without a policy row the legacy columns are what the slice enforces, so
    writing them is enough. Once a server, reseller-plan or hosting-plan
    policy applies it replaces those columns entirely; an edit is then
    recorded as the account-scope override (the most specific policy).
    Only the limits the edit names change: every other field keeps its
    inherited value -- including Unlimited and separate read/write
    bandwidths, which the legacy columns cannot express. resources.policy.reset
    removes the override and returns the account to its plan.
    """
    from daemon import resource_manager
    from shared.models import ResourcePolicy

    override = session.scalar(select(ResourcePolicy).where(
        ResourcePolicy.scope_type == "account", ResourcePolicy.scope_id == account.id,
    ))
    effective = resource_manager.effective_for_account(session, account)
    if effective["policy"] is None and override is None:
        return
    values = dict(effective["values"])
    # The configured value, not the copy capped to this host's CPU count.
    values["cpu_cores"] = effective["configured_cpu_cores"]
    if "cpu_pct" in changed:
        values["cpu_cores"] = changed["cpu_pct"] / 100
    if "mem_mb" in changed:
        values["memory_max_mb"] = changed["mem_mb"]
        high = values.get("memory_high_mb")
        if high is not None and high > changed["mem_mb"]:
            values["memory_high_mb"] = max(64, int(changed["mem_mb"] * .9))
    if "io_mb" in changed:
        values["io_read_bps"] = values["io_write_bps"] = changed["io_mb"] * 1024 * 1024
    if "pids_max" in changed:
        values["nproc"] = changed["pids_max"]
    values = resource_manager.validate_policy(values)
    if override is None:
        override = ResourcePolicy(scope_type="account", scope_id=account.id)
        session.add(override)
    else:
        override.version += 1
    for field, value in values.items():
        setattr(override, field, value)
    # An expired temporary override must not silently swallow the edit.
    override.expires_at = None


def set_limits(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise NotFoundError(f"account '{username}' not found")
        if account.status not in ("active", "suspended"):
            raise ConflictError(f"cannot change limits for an account in status '{account.status}'")

        # A partial edit (omitted or null field) changes only the named
        # limits; the others keep their *enforced* values, which may be
        # Unlimited under a policy and so cannot round-trip a legacy column.
        changed = {}
        for field in LIMIT_FIELDS:
            if params.get(field) is not None:
                try:
                    changed[field] = int(params[field])
                except (TypeError, ValueError):
                    raise ValidationError(f"{field} must be an integer") from None
        if not changed:
            raise ValidationError("Provide at least one limit to change")
        merged = {field: changed.get(field, getattr(account, field)) for field in LIMIT_FIELDS}
        _validate_limits(**merged)

        for field, value in changed.items():
            setattr(account, field, value)
        _write_limits_override(session, account, changed)
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
            raise NotFoundError(f"account '{username}' not found")
        result = _account_to_dict(account)
        prefs = session.scalar(select(AccountNotificationPrefs).where(AccountNotificationPrefs.account_id == account.id))
        result['email'] = prefs.customer_email if prefs else None
        return result


def list_accounts(params: dict) -> dict:
    with write_session() as session:
        # Terminated rows remain as restore/audit tombstones, but they are not
        # active accounts and must not linger in the management list.
        accounts = session.scalars(
            select(Account).where(Account.status != "terminated").order_by(Account.username)
        ).all()
        return {"accounts": [_account_to_dict(a) for a in accounts]}


def customer_login_refusal(session, account_id: int | None) -> str | None:
    """Why a customer login must not get a session, or None.

    For auth.login.begin, after the password check: the API already rejects
    a non-active account's session cookie, so admitting the login only
    produced a session that answered 401 with no explanation.
    """
    account = session.get(Account, account_id) if account_id is not None else None
    if account is None or account.status in ("terminating", "terminated", "error"):
        return "This hosting account is no longer available."
    if account.status == "suspended":
        return "This hosting account is suspended. Contact your hosting provider."
    if account.status != "active":
        # Both resolvers refuse every non-active status, so any status not
        # named above (one being set up, say) is refused here too.
        return "This hosting account is not available right now. Try again in a few minutes."
    return None


def block_system_logins(username: str) -> None:
    """Refuse every Linux login of a suspended account.

    `usermod -L` only invalidates the password hash: sshd still accepts the
    customer's public keys (SSH, SFTP, git-over-SSH) and cron's pam_unix
    account check ignores a password lock, so scheduled jobs kept running.
    An expired account fails the PAM account phase that sshd and cron both
    run (common-account), whatever the authentication method. runuser, which
    the panel itself uses for account-scoped jobs, has no account phase and
    is unaffected. Sessions opened before the suspension are ended too.
    """
    sysops.lock_user(username)
    sysops.run(["usermod", "--expiredate", "1", username], check=True)
    # The per-connection sshd process runs as the user ("sshd: user@pts/0",
    # "sshd-session: ..." on OpenSSH 9.8+); its shell/sftp-server/git
    # children end with it. pkill exits 1 when nothing matched.
    result = sysops.run(["pkill", "-KILL", "-u", username, "-f", "^sshd"], timeout=15)
    if result.returncode not in (0, 1):
        logger.warning("could not end open SSH sessions of suspended account %s", username)


def allow_system_logins(username: str) -> None:
    """Reverse block_system_logins (also clears a resource-quarantine expiry)."""
    sysops.run(["usermod", "--expiredate", "", username], check=True)
    sysops.unlock_user(username)


def _end_running_processes(username: str, uid: int | None) -> None:
    """Stop what a suspended account still runs (cgroups.end_account_processes).

    Ending only the sshd sessions left nohup, tmux and screen processes (and
    cron jobs already started) running for the whole suspension. Best
    effort: logins and the site are already blocked when this runs.
    """
    try:
        cgroups.end_account_processes(username, uid=uid)
    except Exception:  # noqa: BLE001 - the suspension itself stands
        logger.exception("could not end the running processes of suspended account %s", username)


def reconcile_suspended_logins() -> int:
    """Upgrade step: apply the suspension barriers to already-suspended accounts.

    Accounts suspended by a release that only locked the password still
    accept SSH keys, run cron jobs and keep whatever they started running.
    Idempotent; returns how many accounts were reconciled.
    """
    with write_session() as session:
        rows = session.execute(
            select(Account.username, Account.uid).where(Account.status == "suspended", Account.uid.is_not(None))
        ).all()
    reconciled = 0
    for username, uid in rows:
        try:
            block_system_logins(username)
            reconciled += 1
        except Exception:  # noqa: BLE001 - one missing identity must not stop the upgrade
            logger.exception("could not block logins of suspended account %s", username)
            continue
        _end_running_processes(username, uid)
    return reconciled


@account_mutation.locked
def suspend_account(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise NotFoundError(f"account '{username}' not found")
        if account.status == "suspended":
            return _account_to_dict(account)
        if account.status != "active":
            raise ConflictError(f"cannot suspend account in status '{account.status}'")
        from daemon.mail_mutation import require_accounts_available
        require_accounts_available(session, {account.id})

        block_system_logins(username)
        try:
            for hook in SUSPEND_HOOKS:
                hook(account)
        except Exception:
            for hook in UNSUSPEND_HOOKS:
                try:
                    hook(account)
                except Exception:
                    pass
            allow_system_logins(username)
            raise
        # Only once the suspension can no longer roll back: ended processes
        # cannot be brought back.
        _end_running_processes(username, account.uid)

        from shared.models import utcnow

        account.status = "suspended"
        account.suspended_at = utcnow()
        session.flush()
        return _account_to_dict(account)


@account_mutation.locked
def unsuspend_account(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise NotFoundError(f"account '{username}' not found")
        if account.status == "active":
            return _account_to_dict(account)
        if account.status != "suspended":
            raise ConflictError(f"cannot unsuspend account in status '{account.status}'")

        from daemon.resource_manager import effective_for_account
        effective = effective_for_account(session, account)
        if effective["policy"] is None:
            cgroups.apply_limits(username, account.cpu_pct, account.mem_mb, account.io_mb, account.pids_max, uid=account.uid)
        else:
            cgroups.apply_policy(username, effective["values"], uid=account.uid)
        if (account.last_error or "").startswith("Resource enforcement failed;"):
            account.last_error = None
        allow_system_logins(username)
        try:
            for hook in UNSUSPEND_HOOKS:
                hook(account)
        except Exception:
            # SQLite will retain "suspended"; restore external enforcement too.
            block_system_logins(username)
            for hook in SUSPEND_HOOKS:
                try:
                    hook(account)
                except Exception:
                    pass
            raise
        # Suspension ended a lingering systemd --user manager with everything
        # else; PHP workers and the panel's own services need nothing here.
        try:
            cgroups.resume_user_manager(username, uid=account.uid)
        except Exception:  # noqa: BLE001 - the account is usable without it
            logger.exception("could not restart the user manager of unsuspended account %s", username)

        account.status = "active"
        account.suspended_at = None
        session.flush()
        return _account_to_dict(account)


def terminate_account(params: dict) -> dict:
    from daemon import snapshot_jobs as jobs
    username = validate_username(params['username'])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise NotFoundError(f"account '{username}' not found")
        account_id = account.id
    try:
        with jobs.lock(f'account-{account_id}', blocking=False):
            # Wait for the server-wide mutation locks before any teardown:
            # hooks that found them busy used to fail mid-termination and
            # leave databases, zones and the Linux user behind.
            with database_operations.mutation_lock(), dns_operations.mutation_lock():
                return _terminate_account(params)
    except BlockingIOError:
        raise ValidationError('A backup or restore is using this account. Wait for it to finish before termination.') from None


def _release_domain_rows(session, account: Account) -> list[str]:
    """Free a fully terminated account's hostnames for other accounts.

    Domain rows are the ownership record every claim path checks
    (domain_ownership.require_available, the uq_domain constraint), so
    keeping them on a tombstone blocked the names forever. They are removed
    only after every teardown hook succeeded: the vhosts, zones, mail and
    certificates they describe are gone by then. Account.primary_domain
    stays as the historical label. A restore onto the terminated account
    re-adds its domains through domain.add, like an archive import.
    """
    rows = session.scalars(select(Domain).where(Domain.account_id == account.id)).all()
    names = [row.domain for row in rows]
    for row in rows:
        session.delete(row)
    return names


def release_terminated_domains() -> int:
    """Upgrade step: free hostnames still held by already-terminated accounts.

    Releases before this one kept the Domain rows of terminated accounts.
    Idempotent; returns how many names were released.
    """
    with write_session() as session:
        accounts = session.scalars(select(Account).where(Account.status == "terminated")).all()
        names = [name for account in accounts for name in _release_domain_rows(session, account)]
    for name in names:
        _forget_domain_settings(name)
    return len(names)


def _forget_domain_settings(domain_name: str) -> None:
    """Name-keyed per-domain settings that domain.remove also drops.

    Without this a released name would carry the old owner's forwarding,
    routing or wildcard configuration to whoever adds it next. Uses the
    same list as domain.remove, so a setting added there is covered here.
    """
    from daemon import handlers_domain

    failed = handlers_domain.purge_domain_settings(domain_name)
    if failed:  # each step already logged; the account is terminated anyway
        logger.warning("released domain %s kept settings: %s", domain_name, ", ".join(failed))


def _terminate_account(params: dict) -> dict:
    from daemon import snapshot_jobs as jobs
    from shared.models import BackupJob, RestoreJob, SnapshotRun, SnapshotRestore
    username = validate_username(params["username"])
    with jobs.lock('queue'), write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise NotFoundError(f"account '{username}' not found")
        if account.status == "terminated":
            # A successful retry may have converged after an earlier teardown
            # timeout. Do not keep displaying that obsolete error forever.
            if account.last_error is not None:
                account.last_error = None
                session.flush()
            return _account_to_dict(account)
        original_status = account.status
        audit.change("status", original_status, "terminated")
        for model in (BackupJob, RestoreJob, SnapshotRun, SnapshotRestore):
            if session.scalar(select(model.id).where(model.account_id == account.id,
                                                     model.status.in_(jobs.ACTIVE))):
                raise ValidationError('A backup or restore is pending for this account. Complete its recovery before termination.')
        from daemon.mail_mutation import require_accounts_available
        require_accounts_available(session, {account.id})
        account.status = "terminating"
        session.flush()
        account_snapshot = _account_to_dict(account)

    errors: list[str] = []
    released_domains: list[str] = []
    for hook in TERMINATE_HOOKS:
        try:
            hook_name = f"terminate: {hook.__module__}.{hook.__qualname__}"
            with audit.step(hook_name):
                hook(account)  # account is detached but read-only fields (username/id) are safe to use
        except Exception as exc:  # noqa: BLE001 - one hook failing must not block the rest
            logger.exception("terminate hook %s failed for %s", hook, username)
            errors.append(f"{hook.__module__}.{hook.__qualname__}: {exc}")

    # Keep the identity reserved while any service still needs cleanup.
    # Removing it after a failed hook can strand processes/configuration and
    # allow a later Unix UID/name reuse to transfer access to another owner.
    if not errors:
        try:
            with audit.step("terminate: remove account quota"):
                sysops.remove_quota(username)
            with audit.step("terminate: remove Linux account"):
                sysops.delete_linux_user(username)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"linux user teardown: {exc}")

    with audit.step("terminate: revoke panel access and finalize status"):
        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == username))
            from shared.models import utcnow

            if errors:
                account.status = "error"
                account.last_error = "; ".join(errors)
            else:
                account.status = "terminated"
                account.terminated_at = utcnow()
                account.last_error = None
                released_domains = _release_domain_rows(session, account)
                # The Linux user and its home are gone and useradd may give
                # the uid to another tenant; a stale uid on the tombstone made
                # reactivation's identity cross-check fail and mapped that
                # tenant's uid back to this account.
                account.uid = account.gid = None
            audit.change("status", original_status, account.status)

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
            impersonation.end_account_sessions(session, account.id)

            session.flush()
            result = _account_to_dict(account)
    for domain_name in released_domains:
        _forget_domain_settings(domain_name)
    return result


@account_mutation.locked
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
            raise NotFoundError(f"account '{username}' not found")
        if account.status not in ("active", "suspended"):
            raise ConflictError(f"cannot change PHP version for an account in status '{account.status}'")
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
