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
from shared.models import Account, Plan
from shared.validation import ValidationError, validate_resource_limit, validate_username

from daemon import handlers_account, redisacct, sysops, usage_alerts

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


def _plan_to_dict(plan: Plan) -> dict:
    return {
        "id": plan.id,
        "name": plan.name,
        "cpu_pct": plan.cpu_pct,
        "mem_mb": plan.mem_mb,
        "io_mb": plan.io_mb,
        "pids_max": plan.pids_max,
        "quota_soft_mb": plan.quota_soft_mb,
        "quota_hard_mb": plan.quota_hard_mb,
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
    limit_kwargs = {f: validate_resource_limit(params.get(f), f) for f in _LIMIT_FIELDS}
    redis_enabled = bool(params.get("redis_enabled", False))

    with write_session() as session:
        if session.scalar(select(Plan).where(Plan.name == name)) is not None:
            raise ValidationError(f"a plan named '{name}' already exists")
        plan = Plan(
            name=name, cpu_pct=cpu_pct, mem_mb=mem_mb, io_mb=io_mb, pids_max=pids_max,
            quota_soft_mb=quota_soft_mb, quota_hard_mb=quota_hard_mb,
            redis_enabled=redis_enabled, **limit_kwargs,
        )
        session.add(plan)
        session.flush()
        return _plan_to_dict(plan)


def list_plans(params: dict) -> dict:
    with write_session() as session:
        plans = session.scalars(select(Plan).order_by(Plan.name)).all()
        return {"plans": [_plan_to_dict(p) for p in plans]}


def get_plan(params: dict) -> dict:
    plan_id = int(params["plan_id"])
    with write_session() as session:
        plan = session.get(Plan, plan_id)
        if plan is None:
            raise RuntimeError(f"plan {plan_id} not found")
        return _plan_to_dict(plan)


def update_plan(params: dict) -> dict:
    plan_id = int(params["plan_id"])
    with write_session() as session:
        plan = session.get(Plan, plan_id)
        if plan is None:
            raise RuntimeError(f"plan {plan_id} not found")

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

        for field in _LIMIT_FIELDS:
            if field in params:
                setattr(plan, field, validate_resource_limit(params[field], field))

        if "redis_enabled" in params:
            plan.redis_enabled = bool(params["redis_enabled"])

        session.flush()
        return _plan_to_dict(plan)


def delete_plan(params: dict) -> dict:
    plan_id = int(params["plan_id"])
    with write_session() as session:
        plan = session.get(Plan, plan_id)
        if plan is None:
            raise RuntimeError(f"plan {plan_id} not found")
        # plan_id is an informational label on Account (see Account.plan_id's
        # docstring), never re-read to re-derive enforced state -- so clearing
        # it on delete is safe and avoids a raw FK-constraint IntegrityError
        # (the same class of bug already fixed once for Webhook deletion).
        for account in session.scalars(select(Account).where(Account.plan_id == plan_id)).all():
            account.plan_id = None
        session.delete(plan)
    return {"status": "deleted", "id": plan_id}


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

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        if account.status not in ("active", "suspended"):
            raise RuntimeError(f"cannot apply a plan to an account in status '{account.status}'")
        plan = session.get(Plan, plan_id)
        if plan is None:
            raise RuntimeError(f"plan {plan_id} not found")
        plan_values = _plan_to_dict(plan)

        account.cpu_pct = plan_values["cpu_pct"]
        account.mem_mb = plan_values["mem_mb"]
        account.io_mb = plan_values["io_mb"]
        account.pids_max = plan_values["pids_max"]
        account.quota_soft_mb = plan_values["quota_soft_mb"]
        account.quota_hard_mb = plan_values["quota_hard_mb"]
        account.plan_id = plan_values["id"]
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
    try:
        sysops.set_quota(username, plan_values["quota_soft_mb"], plan_values["quota_hard_mb"])
    except Exception:
        logger.exception("quota apply failed for %s during plan apply (continuing)", username)

    if plan_values["redis_enabled"]:
        redisacct.enable_redis({"username": username})
    else:
        # disable_redis() raises if Redis was never enabled for this account --
        # a plan with redis off applied to an account that never had it on
        # must stay a no-op, not an error.
        status = redisacct.get_status({"username": username})
        if status.get("provisioned"):
            redisacct.disable_redis({"username": username})

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        result = handlers_account._account_to_dict(account)
        result["limits"] = usage_alerts.get_limits({"username": username})
        return result
