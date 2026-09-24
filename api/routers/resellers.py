"""Administrator reseller setup and ownership-scoped reseller panel API."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin, require_reseller

admin_router = APIRouter(prefix="/api/v1/admin/resellers", tags=["resellers"])
panel_router = APIRouter(prefix="/api/v1/reseller", tags=["reseller-panel"])


class PlanBody(BaseModel):
    name: str
    max_accounts: int = 10
    max_total_disk_mb: int = 102400
    account_quota_soft_mb: int = 4096
    account_quota_hard_mb: int = 5120
    account_cpu_pct: int = 50
    account_mem_mb: int = 1024
    account_io_mb: int = 50
    account_pids_max: int = 100
    php_version: str = "8.3"


class ResellerCreateBody(BaseModel):
    username: str
    plan_id: int
    company: str | None = None
    password: str | None = None


class ResellerUpdateBody(BaseModel):
    plan_id: int | None = None
    company: str | None = None
    status: str | None = None


class AccountCreateBody(BaseModel):
    username: str
    primary_domain: str | None = None
    password: str | None = None


class AccountMoveBody(BaseModel):
    reseller_id: int | None = None


@admin_router.get("/plans")
def plans(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("reseller.plan.list", identity)


@admin_router.post("/plans")
def create_plan(body: PlanBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("reseller.plan.create", identity, **body.model_dump())


@admin_router.put("/plans/{plan_id}")
def update_plan(plan_id: int, body: PlanBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("reseller.plan.update", identity, plan_id=plan_id, **body.model_dump())


@admin_router.delete("/plans/{plan_id}")
def delete_plan(plan_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("reseller.plan.delete", identity, plan_id=plan_id)


@admin_router.get("")
def list_resellers(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("reseller.list", identity)


@admin_router.post("")
def create_reseller(body: ResellerCreateBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("reseller.create", identity, **body.model_dump(exclude_none=True))


@admin_router.patch("/{reseller_id}")
def update_reseller(reseller_id: int, body: ResellerUpdateBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("reseller.update", identity, reseller_id=reseller_id, **body.model_dump(exclude_none=True))


@admin_router.patch("/accounts/{username}")
def move_account(username: str, body: AccountMoveBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("reseller.account.move", identity, username=username, **body.model_dump())


@panel_router.get("/dashboard")
def dashboard(identity: Identity = Depends(get_identity)):
    require_reseller(identity)
    return call_daemon("reseller.dashboard", identity, reseller_username=identity.username)


@panel_router.post("/accounts")
def create_account(body: AccountCreateBody, identity: Identity = Depends(get_identity)):
    require_reseller(identity)
    return call_daemon("reseller.account.create", identity, reseller_username=identity.username, **body.model_dump(exclude_none=True))


@panel_router.post("/accounts/{username}/{action}")
def lifecycle(username: str, action: str, identity: Identity = Depends(get_identity)):
    require_reseller(identity)
    return call_daemon(
        "reseller.account.lifecycle", identity,
        reseller_username=identity.username, username=username, action=action,
    )
