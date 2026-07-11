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


def _require_confirmed_admin_action(identity: Identity, confirm: bool, totp_code: str | None) -> None:
    """Shared gate for update/rollback: explicit confirm flag, a real browser
    session (API tokens carry panel_user_id=-1 and cannot satisfy a TOTP
    check -- same rule as twofactor.py's _require_session_identity), and a
    fresh TOTP code whenever the admin has 2FA enabled (goal security rule:
    2FA confirmation required for update if enabled)."""
    if not confirm:
        raise HTTPException(status_code=400, detail="pass confirm=true to proceed")
    if identity.panel_user_id is None or identity.panel_user_id < 0:
        raise HTTPException(
            status_code=400,
            detail="panel updates require a browser session, not an API token",
        )
    status = call_daemon("totp.status", identity, panel_user_id=identity.panel_user_id)
    if status.get("enabled"):
        if not totp_code or not totp_code.strip():
            raise HTTPException(
                status_code=400,
                detail="2FA confirmation required: supply totp_code",
            )
        check = call_daemon(
            "totp.check_login_code", identity,
            panel_user_id=identity.panel_user_id, code=totp_code.strip(),
        )
        if not check.get("valid"):
            raise HTTPException(status_code=403, detail="invalid 2FA code")


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
    _require_confirmed_admin_action(identity, body.confirm, body.totp_code)
    return call_daemon(
        "update.start", identity,
        initiated_by=identity.username, to_version=body.to_version,
    )


@admin_api_router.post("/rollback")
def update_rollback(body: RollbackBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    _require_confirmed_admin_action(identity, body.confirm, body.totp_code)
    return call_daemon("update.rollback", identity, initiated_by=identity.username)


@admin_api_router.get("/log")
def update_log(job_id: int | None = None, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("update.log", identity, job_id=job_id)


@admin_api_router.get("/history")
def update_history(limit: int = 50, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("update.history", identity, limit=limit)
