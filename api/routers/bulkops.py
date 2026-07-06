"""Phase 8 feature 12: bulk account operations.
POST /admin/accounts/bulk-action · GET /admin/accounts/bulk-action/{job_id}."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

api_router = APIRouter(prefix="/api/v1/admin/accounts", tags=["bulk-actions"])


class BulkActionBody(BaseModel):
    action: str  # suspend | unsuspend | update_limits | notify
    usernames: list[str]
    action_params: dict = {}


@api_router.post("/bulk-action")
def bulk_action(body: BulkActionBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon(
        "bulk.trigger", identity,
        action=body.action, usernames=body.usernames, action_params=body.action_params,
    )


@api_router.get("/bulk-action/{job_id}")
def bulk_action_status(job_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("bulk.get", identity, job_id=job_id)
