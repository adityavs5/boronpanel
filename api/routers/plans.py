"""Run A feature 1: plan templates. Admin-only configuration resource --
no customer ever reads/writes plans directly (an account's *current*
limits, whichever plan they came from, are already visible via the
existing account/usage-limits endpoints)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

api_router = APIRouter(prefix="/api/v1/admin/plans", tags=["plans"])
apply_api_router = APIRouter(prefix="/api/v1/admin/accounts/{username}/apply-plan", tags=["plans"])


class PlanBody(BaseModel):
    name: str
    cpu_pct: int = 25
    mem_mb: int = 512
    io_mb: int = 50
    pids_max: int = 50
    quota_soft_mb: int = 5120
    quota_hard_mb: int = 6144
    bandwidth_limit_mb: int | None = None
    database_limit: int | None = None
    email_account_limit: int | None = None
    subdomain_limit: int | None = None
    ftp_account_limit: int | None = None
    app_limit: int | None = None
    redis_enabled: bool = False


class PlanUpdateBody(BaseModel):
    name: str | None = None
    cpu_pct: int | None = None
    mem_mb: int | None = None
    io_mb: int | None = None
    pids_max: int | None = None
    quota_soft_mb: int | None = None
    quota_hard_mb: int | None = None
    bandwidth_limit_mb: int | None = None
    database_limit: int | None = None
    email_account_limit: int | None = None
    subdomain_limit: int | None = None
    ftp_account_limit: int | None = None
    app_limit: int | None = None
    redis_enabled: bool | None = None


@api_router.post("")
def create_plan(body: PlanBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("plan.create", identity, **body.model_dump())


@api_router.get("")
def list_plans(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("plan.list", identity)


@api_router.get("/{plan_id}")
def get_plan(plan_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("plan.get", identity, plan_id=plan_id)


@api_router.patch("/{plan_id}")
def update_plan(plan_id: int, body: PlanUpdateBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("plan.update", identity, plan_id=plan_id, **body.model_dump(exclude_unset=True))


@api_router.delete("/{plan_id}")
def delete_plan(plan_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("plan.delete", identity, plan_id=plan_id)


@apply_api_router.post("/{plan_id}")
def apply_plan(username: str, plan_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("plan.apply", identity, username=username, plan_id=plan_id)
