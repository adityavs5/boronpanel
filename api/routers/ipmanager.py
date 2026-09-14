"""Administrator multi-IP inventory, allocation policy, and assignments."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

router = APIRouter(prefix="/api/v1/admin/ip-management", tags=["ip-management"])


class ImportBody(BaseModel):
    addresses: list[str]


class IpUpdateBody(BaseModel):
    allocation_mode: Literal["shared", "dedicated"]
    label: str | None = None
    active: bool = True


class PolicyBody(BaseModel):
    allocation_policy: Literal["primary", "random_shared", "specific"]
    default_server_ip_id: int | None = None


class AssignmentBody(BaseModel):
    selection: Literal["automatic", "primary", "random", "specific", "unassigned"] = "specific"
    server_ip_id: int | None = None


@router.get("")
def get_state(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("ipmanager.list", identity)


@router.post("/import")
def import_addresses(body: ImportBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("ipmanager.import", identity, addresses=body.addresses)


@router.put("/ips/{ip_id}")
def update_ip(ip_id: int, body: IpUpdateBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("ipmanager.update", identity, id=ip_id, **body.model_dump())


@router.delete("/ips/{ip_id}")
def delete_ip(ip_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("ipmanager.delete", identity, id=ip_id)


@router.put("/policy")
def set_policy(body: PolicyBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("ipmanager.policy.set", identity, **body.model_dump())


@router.put("/accounts/{username}")
def assign_account(username: str, body: AssignmentBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("ipmanager.assign", identity, username=username, **body.model_dump())
