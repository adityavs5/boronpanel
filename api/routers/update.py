"""Panel update system: version info + (admin-only) update check/apply/
rollback/history.

`GET /api/v1/version` is authenticated (any role) but deliberately NOT
admin-only: the sidebar footer shows it to customers too. It is not public --
advertising the exact panel version to anonymous scanners makes their job
easier for no benefit (the pre-login page shows the build-time version baked
into the SPA bundle instead, which ships from the same version.py at release
time).

Everything under /api/v1/admin/update/* is admin-only and thin: the daemon
owns all update logic (it's the only process allowed to touch /opt, systemd,
and the GitHub download path); this router just authorizes and forwards.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin
from version import BORON_VERSION

api_router = APIRouter(prefix="/api/v1", tags=["update"])
admin_api_router = APIRouter(prefix="/api/v1/admin/update", tags=["update"])


@api_router.get("/version")
def get_version(identity: Identity = Depends(get_identity)):
    return {"version": BORON_VERSION}


class StartUpdateBody(BaseModel):
    confirm: bool = False
    totp_code: str | None = None
    to_version: str | None = None


class RollbackBody(BaseModel):
    confirm: bool = False
    totp_code: str | None = None


def _require_confirmed_admin_action(identity: Identity, confirm: bool) -> None:
    """Give immediate UI feedback; borond repeats this and verifies 2FA."""
    if not confirm:
        raise HTTPException(status_code=400, detail="pass confirm=true to proceed")
    if identity.panel_user_id is None or identity.panel_user_id < 0:
        raise HTTPException(
            status_code=400,
            detail="panel updates require a browser session, not an API token",
        )


@admin_api_router.get("/status")
def update_status(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("update.status", identity)


@admin_api_router.post("/check")
def update_check_now(identity: Identity = Depends(get_identity)):
    """The UI's "Check now" button -- bypasses the 1h cache."""
    require_admin(identity)
    return call_daemon("update.check", identity, force=True)


@admin_api_router.post("/start")
def update_start(body: StartUpdateBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    _require_confirmed_admin_action(identity, body.confirm)
    return call_daemon(
        "update.start", identity,
        initiated_by=identity.username, to_version=body.to_version,
        confirm=body.confirm, totp_code=body.totp_code,
    )


@admin_api_router.post("/rollback")
def update_rollback(body: RollbackBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    _require_confirmed_admin_action(identity, body.confirm)
    return call_daemon("update.rollback", identity, initiated_by=identity.username,
                       confirm=body.confirm, totp_code=body.totp_code)


@admin_api_router.get("/log")
def update_log(job_id: int | None = None, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("update.log", identity, job_id=job_id)


@admin_api_router.get("/history")
def update_history(limit: int = 50, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("update.history", identity, limit=limit)
