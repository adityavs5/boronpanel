from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/usage", tags=["usage"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/usage", tags=["ui:usage"])


@api_router.get("")
def get_usage(username: str, force_refresh: bool = False, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("usage.get", identity, username=username, force_refresh=force_refresh)


@ui_router.get("")
def ui_usage(request: Request, username: str, force_refresh: bool = False, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    data = call_daemon("usage.get", identity, username=username, force_refresh=force_refresh)
    return templates.TemplateResponse(request, "usage.html", {"identity": identity, "username": username, "usage": data})
