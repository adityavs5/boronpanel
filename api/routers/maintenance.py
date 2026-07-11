"""Missing-features batch, goal feature 2: per-domain maintenance mode.
PATCH /accounts/{u}/domains/{d}/maintenance (the goal's own literal shape),
plus an admin-only overview of every domain currently in maintenance."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_admin, require_domain_access

api_router = APIRouter(prefix="/api/v1/accounts/{username}/domains/{domain}/maintenance", tags=["maintenance"])
admin_api_router = APIRouter(prefix="/api/v1/admin/maintenance", tags=["maintenance"])


class MaintenanceBody(BaseModel):
    enabled: bool = True
    title: str = "We'll be right back"
    message: str = "This site is currently undergoing scheduled maintenance. Please check back soon."
    estimated_time: str = ""
    auto_disable_minutes: int | None = None
    regenerate_token: bool = False


@api_router.get("")
def get_maintenance(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("maintenance.get", identity, domain=domain)


@api_router.patch("")
def set_maintenance(username: str, domain: str, body: MaintenanceBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("maintenance.set", identity, domain=domain, **body.model_dump())


@admin_api_router.get("")
def list_active_maintenance(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("maintenance.list_active", identity)
