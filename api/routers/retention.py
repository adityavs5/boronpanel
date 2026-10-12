"""Administrator retention policy and bounded prune controls."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

api_router = APIRouter(prefix="/api/v1/admin/retention", tags=["retention"])


class RetentionBody(BaseModel):
    audit_days: int | None = None
    action_step_days: int | None = None
    task_output_days: int | None = None
    usage_raw_days: int | None = None
    usage_monthly_days: int | None = None
    bandwidth_days: int | None = None
    daemon_log_days: int | None = None
    support_bundle_days: int | None = None
    investigation_hold: bool | None = None


@api_router.get("")
def get_retention(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("retention.get", identity)


@api_router.patch("")
def set_retention(body: RetentionBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("retention.set", identity, **body.model_dump(exclude_unset=True))


@api_router.post("/prune")
def run_retention(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("retention.prune", identity)
