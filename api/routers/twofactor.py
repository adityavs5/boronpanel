"""Phase 5 feature 10: TOTP two-factor authentication.

Self-service only -- every endpoint here acts on the calling identity's
own panel user (`identity.panel_user_id`), never a body-supplied user id,
the same "self-service, own-account only" rule `auth.py`'s
change-password endpoint already establishes. Available to both admin
and customer identities (goal: required for admin, optional for
customer) -- "required" for admin is a policy/UI expectation, not a
technical login-time gate against pre-existing admin credentials that
predate this feature (see docs/CHECKPOINT-phase5-10-totp.md for why).
"""
from __future__ import annotations

import base64
import io

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity
from api.templates import templates
from shared.db import read_session
from shared.models import PanelUser
from shared.passwords import verify_password

api_router = APIRouter(prefix="/api/v1/2fa", tags=["2fa"])
ui_router = APIRouter(prefix="/ui/2fa", tags=["ui:2fa"])


def _require_session_identity(identity: Identity) -> None:
    if identity.panel_user_id < 0:
        raise HTTPException(status_code=400, detail="2FA setup requires a browser session, not an API token")


def _qr_data_uri(otpauth_uri: str) -> str:
    img = qrcode.make(otpauth_uri, image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    encoded = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/svg+xml;base64,{encoded}"


class VerifyBody(BaseModel):
    code: str


class DisableBody(BaseModel):
    current_password: str


@api_router.get("/status")
def get_status(identity: Identity = Depends(get_identity)):
    _require_session_identity(identity)
    return call_daemon("totp.status", identity, panel_user_id=identity.panel_user_id)


@api_router.post("/setup")
def setup(identity: Identity = Depends(get_identity)):
    _require_session_identity(identity)
    result = call_daemon("totp.setup", identity, panel_user_id=identity.panel_user_id)
    return {**result, "qr_data_uri": _qr_data_uri(result["otpauth_uri"])}


@api_router.post("/verify")
def verify(body: VerifyBody, identity: Identity = Depends(get_identity)):
    _require_session_identity(identity)
    return call_daemon("totp.verify", identity, panel_user_id=identity.panel_user_id, code=body.code)


@api_router.post("/disable")
def disable(body: DisableBody, identity: Identity = Depends(get_identity)):
    _require_session_identity(identity)
    with read_session() as db:
        user = db.get(PanelUser, identity.panel_user_id)
        current_ok = user is not None and verify_password(body.current_password, user.password_hash)
    if not current_ok:
        raise HTTPException(status_code=401, detail="current password is incorrect")
    return call_daemon("totp.disable", identity, panel_user_id=identity.panel_user_id,
                       current_password=body.current_password)


# --- server-rendered UI ------------------------------------------------------


@ui_router.get("")
def ui_2fa(request: Request, identity: Identity = Depends(get_identity)):
    _require_session_identity(identity)
    status = call_daemon("totp.status", identity, panel_user_id=identity.panel_user_id)
    return templates.TemplateResponse(request, "twofactor_setup.html", {"identity": identity, "status": status, "setup": None, "recovery_codes": None, "error": None})


@ui_router.post("/setup")
def ui_setup(request: Request, identity: Identity = Depends(get_identity)):
    _require_session_identity(identity)
    status = call_daemon("totp.status", identity, panel_user_id=identity.panel_user_id)
    result = call_daemon("totp.setup", identity, panel_user_id=identity.panel_user_id)
    setup_data = {**result, "qr_data_uri": _qr_data_uri(result["otpauth_uri"])}
    return templates.TemplateResponse(request, "twofactor_setup.html", {"identity": identity, "status": status, "setup": setup_data, "recovery_codes": None, "error": None})


@ui_router.post("/verify")
def ui_verify(request: Request, code: str = Form(...), identity: Identity = Depends(get_identity)):
    _require_session_identity(identity)
    try:
        result = call_daemon("totp.verify", identity, panel_user_id=identity.panel_user_id, code=code)
    except HTTPException as exc:
        status = call_daemon("totp.status", identity, panel_user_id=identity.panel_user_id)
        return templates.TemplateResponse(
            request, "twofactor_setup.html", {"identity": identity, "status": status, "setup": None, "recovery_codes": None, "error": exc.detail}, status_code=exc.status_code
        )
    status = call_daemon("totp.status", identity, panel_user_id=identity.panel_user_id)
    return templates.TemplateResponse(
        request, "twofactor_setup.html", {"identity": identity, "status": status, "setup": None, "recovery_codes": result["recovery_codes"], "error": None}
    )


@ui_router.post("/disable")
def ui_disable(current_password: str = Form(...), identity: Identity = Depends(get_identity)):
    _require_session_identity(identity)
    with read_session() as db:
        user = db.get(PanelUser, identity.panel_user_id)
        current_ok = user is not None and verify_password(current_password, user.password_hash)
    if current_ok:
        call_daemon("totp.disable", identity, panel_user_id=identity.panel_user_id,
                    current_password=current_password)
    return RedirectResponse("/ui/2fa", status_code=303)
