from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from starlette.requests import Request

from shared.db import read_session
from shared.models import ApiToken, PanelUser

from api.rpc import call_daemon
from api.security import API_TOKEN_MAX_AGE_SECONDS, Identity, get_identity, require_admin
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/tokens", tags=["tokens"])
ui_router = APIRouter(prefix="/ui/tokens", tags=["ui:tokens"])


class CreateTokenBody(BaseModel):
    label: str
    role: str = "admin"
    account_id: int | None = None
    scope: str = 'full'
    expires_in_days: int = 30


def _require_admin_session(identity: Identity) -> None:
    """Token management needs an interactive administrator session.

    A Bearer token that could mint or revoke tokens would let one leaked
    credential create descendants that survive its own revocation.
    """
    require_admin(identity)
    if identity.auth_method != "session" or identity.panel_user_id <= 0 or identity.is_impersonating:
        raise HTTPException(status_code=403, detail="A current administrator session is required")


@api_router.get("")
def list_tokens(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    with read_session() as db:
        rows = db.scalars(select(ApiToken)).all()
        creators = dict(db.execute(select(PanelUser.id, PanelUser.username)).all())
        return [
            {"id": r.id, "label": r.label, "role": r.role, "account_id": r.account_id,
             # Who minted it -- the identity the token acts as. The label is
             # free text and must not be read as an owner.
             "created_by": creators.get(r.created_by_panel_user_id),
             "revoked": r.revoked_at is not None,
             "scope": r.scope,
             "expires_at": (r.expires_at or (r.created_at + __import__("datetime").timedelta(seconds=API_TOKEN_MAX_AGE_SECONDS))).isoformat()}
            for r in rows
        ]


@api_router.post("")
def create_token(body: CreateTokenBody, identity: Identity = Depends(get_identity)):
    _require_admin_session(identity)
    return call_daemon("auth.create_api_token", identity, **body.model_dump(exclude_none=True))


@api_router.delete("/{token_id}")
def revoke_token(token_id: int, identity: Identity = Depends(get_identity)):
    _require_admin_session(identity)
    return call_daemon("auth.revoke_api_token", identity, token_id=token_id)


@ui_router.get("")
def ui_list_tokens(request: Request, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    with read_session() as db:
        rows = db.scalars(select(ApiToken)).all()
    return templates.TemplateResponse(request, "tokens.html", {"identity": identity, "tokens": rows, "new_token": None})


@ui_router.post("")
def ui_create_token(request: Request, label: str = Form(...), identity: Identity = Depends(get_identity)):
    _require_admin_session(identity)
    result = call_daemon("auth.create_api_token", identity, label=label, role="admin")
    with read_session() as db:
        rows = db.scalars(select(ApiToken)).all()
    return templates.TemplateResponse(
        request, "tokens.html", {"identity": identity, "tokens": rows, "new_token": result["token"]}
    )


@ui_router.post("/{token_id}/revoke")
def ui_revoke_token(token_id: int, identity: Identity = Depends(get_identity)):
    _require_admin_session(identity)
    call_daemon("auth.revoke_api_token", identity, token_id=token_id)
    return RedirectResponse("/ui/tokens", status_code=303)
