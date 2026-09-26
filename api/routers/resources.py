from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

router = APIRouter(prefix="/api/v1/admin/resources", tags=["resources"])


class PolicyBody(BaseModel):
    cpu_cores: float | None = None
    cpu_weight: int | None = 100
    memory_high_mb: int | None = None
    memory_max_mb: int | None = None
    io_read_bps: int | None = None
    io_write_bps: int | None = None
    io_read_iops: int | None = None
    io_write_iops: int | None = None
    nproc: int | None = None
    entry_processes: int | None = None
    expires_at: str | None = None


@router.get("")
def overview(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("resources.overview", identity)


@router.put("/server")
def set_server_policy(body: PolicyBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("resources.policy.save", identity, scope_type="server", scope_id=0, **body.model_dump())


@router.put("/plans/{plan_id}")
def set_plan_policy(plan_id: int, body: PolicyBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("resources.policy.save", identity, scope_type="plan", scope_id=plan_id, **body.model_dump())


@router.put("/accounts/{username}")
def set_account_policy(username: str, body: PolicyBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("resources.policy.account", identity, username=username, **body.model_dump())


@router.delete("/accounts/{username}")
def reset_account_policy(username: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("resources.policy.reset", identity, username=username)


@router.post("/sample")
def sample(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("resources.sample", identity)
