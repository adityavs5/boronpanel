"""Administrator license page API. Always reachable, even while unlicensed."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin
from shared import license as license_state

router = APIRouter(prefix="/api/v1/admin/license", tags=["license"])


class ActivateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    license_key: str = Field(min_length=1, max_length=64)


@router.get("")
def get_license(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("license.status", identity)


@router.get("/state")
def get_state(identity: Identity = Depends(get_identity)):
    """Cheap, local-only state for the admin banner; no daemon round trip."""
    require_admin(identity)
    state = license_state.current_state()
    return {"state": state["state"], "usable": state["usable"], "reason": state["reason"],
            "message": state["message"]}


@router.post("")
def activate(body: ActivateBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("license.activate", identity, license_key=body.license_key)


@router.post("/refresh")
def refresh(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("license.refresh", identity)
