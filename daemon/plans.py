"""Run A feature 1: named hosting-plan presets (Basic/Pro/Business/...).

CRUD on the `Plan` template table plus `apply_plan`, the one operation that
actually mutates an account: cPanel-package-style "apply this preset to
this account, atomically." Deliberately thin -- every individual limit is
already owned by an existing module (cgroup limits by
daemon.handlers_account, quota by daemon.sysops, bandwidth/database/email/
subdomain/ftp/app limits by daemon.usage_alerts, Redis by
daemon.redisacct); this module only validates the plan itself and
orchestrates calls into those, rather than re-implementing any of them.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, Plan, ResourcePolicy
from shared.validation import ConflictError, NotFoundError, ValidationError, validate_resource_limit, validate_username

from daemon import handlers_account, inode_quotas, redisacct, resource_manager, sysops, usage_alerts
from daemon.database_operations import serialized

logger = logging.getLogger("borond.plans")

_LIMIT_FIELDS = (
    "bandwidth_limit_mb", "database_limit", "email_account_limit",
    "subdomain_limit", "ftp_account_limit", "app_limit",
)


def _validate_name(name) -> str:
    name = (name or "").strip()
    if not name:
        raise ValidationError("plan name is required")
    if len(name) > 64:
        raise ValidationError("plan name must be at most 64 characters")
    return name


def _validate_quota(soft_mb, hard_mb) -> tuple[int, int]:
    try:
        soft_mb, hard_mb = int(soft_mb), int(hard_mb)
    except (TypeError, ValueError):
        raise ValidationError("quota_soft_mb/quota_hard_mb must be integers") from None
    if soft_mb < 1 or hard_mb < 1:
        raise ValidationError("quota_soft_mb/quota_hard_mb must be positive")
    if hard_mb < soft_mb:
        raise ValidationError("quota_hard_mb must be >= quota_soft_mb")
    return soft_mb, hard_mb


def _plan_to_dict(plan: Plan, policy: ResourcePolicy | None = None) -> dict:
    result = {
        "id": plan.id,
        "name": plan.name,
        "cpu_pct": plan.cpu_pct,
        "cpu_cores": plan.cpu_pct / 100,
        "mem_mb": plan.mem_mb,
        "io_mb": plan.io_mb,
        "pids_max": plan.pids_max,
        "quota_soft_mb": plan.quota_soft_mb,
        "quota_hard_mb": plan.quota_hard_mb,
        "inode_soft_limit": plan.inode_soft_limit,
        "inode_hard_limit": plan.inode_hard_limit,
        "bandwidth_limit_mb": plan.bandwidth_limit_mb,
        "database_limit": plan.database_limit,
        "email_account_limit": plan.email_account_limit,
        "subdomain_limit": plan.subdomain_limit,
        "ftp_account_limit": plan.ftp_account_limit,
        "app_limit": plan.app_limit,
        "redis_enabled": plan.redis_enabled,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
        "updated_at": plan.updated_at.isoformat() if plan.updated_at else None,
    }
    if policy is not None:
        result.update({field: getattr(policy, field) for field in resource_manager.POLICY_FIELDS})
    return result


def _resource_values(params: dict, *, cpu_pct: int, mem_mb: int, io_mb: int, pids_max: int) -> dict:
    raw = {
        "cpu_cores": params.get("cpu_cores", cpu_pct / 100),
        "cpu_weight": params.get("cpu_weight", 100),
        "memory_high_mb": params.get("memory_high_mb", max(64, int(mem_mb * .9))),
        "memory_max_mb": params.get("memory_max_mb", mem_mb),
        "io_read_bps": params.get("io_read_bps", io_mb * 1024 * 1024),
        "io_write_bps": params.get("io_write_bps", io_mb * 1024 * 1024),
        "io_read_iops": params.get("io_read_iops", resource_manager.DEFAULT_READ_IOPS),
        "io_write_iops": params.get("io_write_iops", resource_manager.DEFAULT_WRITE_IOPS),
        "nproc": params.get("nproc", pids_max),
        "entry_processes": params.get("entry_processes", 20),
    }
    return resource_manager.validate_policy(raw)


def create_plan(params: dict) -> dict:
    name = _validate_name(params.get("name"))
    cpu_pct = int(params.get("cpu_pct", 25))
    mem_mb = int(params.get("mem_mb", 512))
    io_mb = int(params.get("io_mb", 50))
    pids_max = int(params.get("pids_max", 50))
    handlers_account._validate_limits(cpu_pct, mem_mb, io_mb, pids_max)
    quota_soft_mb, quota_hard_mb = _validate_quota(
        params.get("quota_soft_mb", 5120), params.get("quota_hard_mb", 6144)
    )
    inode_soft_limit, inode_hard_limit = inode_quotas.validate_limits(
        params.get("inode_soft_limit", inode_quotas.DEFAULT_SOFT),
        params.get("inode_hard_limit", inode_quotas.DEFAULT_HARD),
    )
    limit_kwargs = {f: validate_resource_limit(params.get(f), f) for f in _LIMIT_FIELDS}
    redis_enabled = bool(params.get("redis_enabled", False))

    with write_session() as session:
        if session.scalar(select(Plan).where(Plan.name == name)) is not None:
            raise ValidationError(f"a plan named '{name}' already exists")
        plan = Plan(
            name=name, cpu_pct=cpu_pct, mem_mb=mem_mb, io_mb=io_mb, pids_max=pids_max,
            quota_soft_mb=quota_soft_mb, quota_hard_mb=quota_hard_mb,
            inode_soft_limit=inode_soft_limit, inode_hard_limit=inode_hard_limit,
            redis_enabled=redis_enabled, **limit_kwargs,
        )
        session.add(plan)
        session.flush()
        policy = ResourcePolicy(scope_type="plan", scope_id=plan.id, **_resource_values(
            params, cpu_pct=cpu_pct, mem_mb=mem_mb, io_mb=io_mb, pids_max=pids_max,
        ))
        session.add(policy)
        session.flush()
        return _plan_to_dict(plan, policy)


def list_plans(params: dict) -> dict:
    with write_session() as session:
        plans = session.scalars(select(Plan).order_by(Plan.name)).all()
        policies = {row.scope_id: row for row in session.scalars(select(ResourcePolicy).where(ResourcePolicy.scope_type == "plan")).all()}
        return {
            "plans": [_plan_to_dict(p, policies.get(p.id)) for p in plans],
            "host_cpu_cores": resource_manager.available_cpu_cores(),
        }


def get_plan(params: dict) -> dict:
    plan_id = int(params["plan_id"])
    with write_session() as session:
        plan = session.get(Plan, plan_id)
        if plan is None:
            raise NotFoundError(f"plan {plan_id} not found")
        policy = session.scalar(select(ResourcePolicy).where(ResourcePolicy.scope_type == "plan", ResourcePolicy.scope_id == plan.id))
        return _plan_to_dict(plan, policy)


@serialized
def update_plan(params: dict) -> dict:
    plan_id = int(params["plan_id"])
    with write_session() as session:
        plan = session.get(Plan, plan_id)
        if plan is None:
            raise NotFoundError(f"plan {plan_id} not found")

        if "name" in params:
            new_name = _validate_name(params["name"])
            if new_name != plan.name and session.scalar(select(Plan).where(Plan.name == new_name)) is not None:
                raise ValidationError(f"a plan named '{new_name}' already exists")
            plan.name = new_name

        cpu_pct = int(params.get("cpu_pct", plan.cpu_pct))
        mem_mb = int(params.get("mem_mb", plan.mem_mb))
        io_mb = int(params.get("io_mb", plan.io_mb))
        pids_max = int(params.get("pids_max", plan.pids_max))
        handlers_account._validate_limits(cpu_pct, mem_mb, io_mb, pids_max)
        plan.cpu_pct, plan.mem_mb, plan.io_mb, plan.pids_max = cpu_pct, mem_mb, io_mb, pids_max

        quota_soft_mb, quota_hard_mb = _validate_quota(
            params.get("quota_soft_mb", plan.quota_soft_mb), params.get("quota_hard_mb", plan.quota_hard_mb)
        )
        plan.quota_soft_mb, plan.quota_hard_mb = quota_soft_mb, quota_hard_mb
        inode_soft_limit, inode_hard_limit = inode_quotas.validate_limits(
            params.get("inode_soft_limit", plan.inode_soft_limit),
            params.get("inode_hard_limit", plan.inode_hard_limit),
        )
        plan.inode_soft_limit, plan.inode_hard_limit = inode_soft_limit, inode_hard_limit

        for field in _LIMIT_FIELDS:
            if field in params:
                setattr(plan, field, validate_resource_limit(params[field], field))

        redis_changed = "redis_enabled" in params and bool(params["redis_enabled"]) != bool(plan.redis_enabled)
        if "redis_enabled" in params:
            plan.redis_enabled = bool(params["redis_enabled"])

        policy = session.scalar(select(ResourcePolicy).where(
            ResourcePolicy.scope_type == "plan", ResourcePolicy.scope_id == plan.id,
        ))
        resource_input = ({field: getattr(policy, field) for field in resource_manager.POLICY_FIELDS}
                          if policy is not None else {})
        resource_input.update(params)
        values = _resource_values(resource_input, cpu_pct=cpu_pct, mem_mb=mem_mb, io_mb=io_mb, pids_max=pids_max)
        if policy is None:
            policy = ResourcePolicy(scope_type="plan", scope_id=plan.id)
            session.add(policy)
        else:
            policy.version += 1
        for field, value in values.items():
            setattr(policy, field, value)

        session.flush()
        result = _plan_to_dict(plan, policy)
        assigned = list(session.scalars(select(Account.username).where(
            Account.plan_id == plan_id, Account.status.in_(["active", "suspended"]),
        ).order_by(Account.username)).all())
    # cPanel package semantics: an edit reaches every assigned account, not
    # only the cgroup policy. Quota, inode, usage and Redis settings used to
    # stay at the values copied when the plan was first applied. Per-account
    # limit overrides (account.set_limits) are kept.
    failures = []
    for username in assigned:
        try:
            _apply_to_account(username, plan_id, redis=redis_changed)
        except Exception as exc:  # noqa: BLE001 - report, keep updating the other accounts
            logger.exception("could not propagate plan %s to %s", plan_id, username)
            failures.append({"username": username, "error": str(exc) if isinstance(exc, ValidationError)
                             else "internal failure; see the daemon log"})
    result["accounts_updated"] = len(assigned) - len(failures)
    result["propagation_failures"] = failures
    return result


def delete_plan(params: dict) -> dict:
    plan_id = int(params["plan_id"])
    with write_session() as session:
        plan = session.get(Plan, plan_id)
        if plan is None:
            raise NotFoundError(f"plan {plan_id} not found")
        # A plan's policy row is what live accounts enforce
        # (resource_manager.effective_for_account reads Account.plan_id).
        # Deleting it under them silently fell back to their legacy limits at
        # the next suspend/unsuspend or limits change, so an assigned plan is
        # refused; move the accounts to another plan first.
        in_use = session.scalars(select(Account.username).where(
            Account.plan_id == plan_id, Account.status != "terminated",
        ).order_by(Account.username)).all()
        if in_use:
            shown = ", ".join(in_use[:5]) + (f" and {len(in_use) - 5} more" if len(in_use) > 5 else "")
            raise ConflictError(
                f"plan '{plan.name}' is assigned to {len(in_use)} account(s) ({shown}); "
                "apply another plan to them before deleting it"
            )
        # Terminated tombstones only keep the label; clear it so the
        # foreign key does not block the delete.
        for account in session.scalars(select(Account).where(Account.plan_id == plan_id)).all():
            account.plan_id = None
        policy = session.scalar(select(ResourcePolicy).where(
            ResourcePolicy.scope_type == "plan", ResourcePolicy.scope_id == plan_id,
        ))
        if policy:
            session.delete(policy)
        session.delete(plan)
    return {"status": "deleted", "id": plan_id}


@serialized
def apply_plan(params: dict) -> dict:
    """Atomically apply a plan's values to one account: validate account +
    plan exist first, then commit every DB-row write (Account's own
    columns, AccountResourceLimits, Account.plan_id) before touching any
    external system (cgroups/quota/Redis) -- same "DB write, then external
    side effect" tradeoff ARCHITECTURE.md SS4 already documents accepting
    elsewhere. An external step failing raises (dispatch() logs it to the
    audit log as a failed op) rather than being silently swallowed.
    """
    username = validate_username(params["username"])
    plan_id = int(params["plan_id"])
    return _apply_to_account(username, plan_id, reset_override=True, redis=True)


def _legacy_limits(plan_values: dict) -> tuple[int, int, int, int]:
    """Account columns that mirror what the plan's policy enforces.

    Backups, restores and the no-policy fallback read these columns, so they
    should describe the enforced cpu/memory/IO/process values (the policy),
    not the plan's separate legacy defaults (25%/512 MB unless edited).
    Values a column cannot express (Unlimited, out of range) keep the plan's
    legacy value.
    """
    legacy = (plan_values["cpu_pct"], plan_values["mem_mb"], plan_values["io_mb"], plan_values["pids_max"])
    cpu, memory, read_bps, nproc = (plan_values.get(field) for field in
                                    ("cpu_cores", "memory_max_mb", "io_read_bps", "nproc"))
    candidate = (
        legacy[0] if cpu is None else round(cpu * 100),
        legacy[1] if memory is None else int(memory),
        legacy[2] if read_bps is None else max(1, round(read_bps / 1048576)),
        legacy[3] if nproc is None else int(nproc),
    )
    try:
        handlers_account._validate_limits(*candidate)
    except ValidationError:
        return legacy
    return candidate


def _sync_redis(username: str, enabled: bool) -> None:
    status = redisacct.get_status({"username": username})
    if enabled:
        if not status.get("enabled"):
            redisacct.enable_redis({"username": username})
    elif status.get("provisioned"):
        # disable_redis() raises if Redis was never enabled for this account --
        # a plan with redis off applied to an account that never had it on
        # must stay a no-op, not an error.
        redisacct.disable_redis({"username": username})


def _apply_to_account(username: str, plan_id: int, *, reset_override: bool = False, redis: bool = True) -> dict:
    """Make one account match a plan (plan.apply, and every plan.update).

    reset_override: an explicit plan.apply resets the account to the
    package, dropping a per-account limits override; propagation of a plan
    edit keeps it. redis: whether to reconcile Redis (propagation does so
    only when the plan's Redis setting changed, to avoid restarts).
    """
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise NotFoundError(f"account '{username}' not found")
        if account.status not in ("active", "suspended"):
            raise ConflictError(f"cannot apply a plan to an account in status '{account.status}'")
        plan = session.get(Plan, plan_id)
        if plan is None:
            raise NotFoundError(f"plan {plan_id} not found")
        policy = session.scalar(select(ResourcePolicy).where(ResourcePolicy.scope_type == "plan", ResourcePolicy.scope_id == plan.id))
        plan_values = _plan_to_dict(plan, policy)

        account.cpu_pct, account.mem_mb, account.io_mb, account.pids_max = _legacy_limits(plan_values)
        account.quota_soft_mb = plan_values["quota_soft_mb"]
        account.quota_hard_mb = plan_values["quota_hard_mb"]
        # Re-applying unchanged inode limits must not restart the warning
        # period the customer was already given.
        if (account.inode_soft_limit, account.inode_hard_limit) != (
            plan_values["inode_soft_limit"], plan_values["inode_hard_limit"]
        ):
            inode_quotas.schedule(
                account, plan_values["inode_soft_limit"], plan_values["inode_hard_limit"]
            )
        account.plan_id = plan_values["id"]
        if reset_override:
            override = session.scalar(select(ResourcePolicy).where(
                ResourcePolicy.scope_type == "account", ResourcePolicy.scope_id == account.id,
            ))
            if override is not None:
                session.delete(override)
        session.flush()
        account_snapshot = account

    # AccountResourceLimits' six fields, one call, its own transaction --
    # reuses usage_alerts' own validation/write logic rather than duplicating it.
    usage_alerts.set_limits({
        "username": username,
        **{f: plan_values[f] for f in _LIMIT_FIELDS},
    })

    # External side effects: cgroups, quota, Redis.
    for hook in handlers_account.LIMITS_HOOKS:
        hook(account_snapshot)
    sysops.set_quota(username, plan_values["quota_soft_mb"], plan_values["quota_hard_mb"])

    if redis:
        _sync_redis(username, bool(plan_values["redis_enabled"]))

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        result = handlers_account._account_to_dict(account)
        result["limits"] = usage_alerts.get_limits({"username": username})
        return result
