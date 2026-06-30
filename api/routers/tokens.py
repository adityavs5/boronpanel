from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from starlette.requests import Request

from shared.db import read_session
from shared.models import ApiToken

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/tokens", tags=["tokens"])
ui_router = APIRouter(prefix="/ui/tokens", tags=["ui:tokens"])


class CreateTokenBody(BaseModel):
    label: str
    role: str = "admin"
    account_id: int | None = None


@api_router.get("")
def list_tokens(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    with read_session() as db:
        rows = db.scalars(select(ApiToken)).all()
        return [
            {"id": r.id, "label": r.label, "role": r.role, "account_id": r.account_id, "revoked": r.revoked_at is not None}
            for r in rows
        ]


@api_router.post("")
def create_token(body: CreateTokenBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("auth.create_api_token", identity, **body.model_dump(exclude_none=True))


@api_router.delete("/{token_id}")
def revoke_token(token_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("auth.revoke_api_token", identity, token_id=token_id)


@ui_router.get("")
def ui_list_tokens(request: Request, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    with read_session() as db:
        rows = db.scalars(select(ApiToken)).all()
    return templates.TemplateResponse(request, "tokens.html", {"identity": identity, "tokens": rows, "new_token": None})


@ui_router.post("")
def ui_create_token(request: Request, label: str = Form(...), identity: Identity = Depends(get_identity)):
    require_admin(identity)
    result = call_daemon("auth.create_api_token", identity, label=label, role="admin")
    with read_session() as db:
        rows = db.scalars(select(ApiToken)).all()
    return templates.TemplateResponse(
        request, "tokens.html", {"identity": identity, "tokens": rows, "new_token": result["token"]}
    )


@ui_router.post("/{token_id}/revoke")
def ui_revoke_token(token_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("auth.revoke_api_token", identity, token_id=token_id)
    return RedirectResponse("/ui/tokens", status_code=303)
