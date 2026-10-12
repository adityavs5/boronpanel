"""Current-login session management and administrator 2FA policy."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

api_router = APIRouter(prefix="/api/v1/security", tags=["security"])
admin_router = APIRouter(prefix="/api/v1/admin/security-policy", tags=["security"])


class AdminPolicyBody(BaseModel):
    require_admin_2fa: bool | None = None
    admin_2fa_grace_days: int | None = None


@api_router.get("/sessions")
def list_sessions(identity: Identity = Depends(get_identity)):
    return call_daemon("sessions.list", identity, panel_user_id=identity.panel_user_id)


@api_router.delete("/sessions/{session_id}")
def revoke_session(session_id: int, identity: Identity = Depends(get_identity)):
    return call_daemon(
        "sessions.revoke", identity,
        panel_user_id=identity.panel_user_id, session_row_id=session_id,
    )


@api_router.post("/sessions/revoke-others")
def revoke_other_sessions(identity: Identity = Depends(get_identity)):
    return call_daemon("sessions.revoke_others", identity, panel_user_id=identity.panel_user_id)


@admin_router.get("")
def get_admin_policy(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("security.policy.get", identity)


@admin_router.patch("")
def set_admin_policy(body: AdminPolicyBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon(
        "security.policy.set", identity, **body.model_dump(exclude_unset=True)
    )
