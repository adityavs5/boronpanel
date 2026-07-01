from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/logs", tags=["logs"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/logs", tags=["ui:logs"])


@api_router.get("/{type}")
def get_log(username: str, type: str, domain: str | None = None, severity: str | None = None, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    params = {"username": username, "type": type}
    if domain:
        params["domain"] = domain
    if severity:
        params["severity"] = severity
    return call_daemon("logs.get", identity, **params)


@ui_router.get("")
def ui_logs_home(
    request: Request,
    username: str,
    type: str = "ols",
    severity: str = "",
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    params = {"username": username, "type": type}
    if severity:
        params["severity"] = severity
    result = call_daemon("logs.get", identity, **params)
    return templates.TemplateResponse(
        request,
        "logs.html",
        {"identity": identity, "username": username, "type": type, "severity": severity, "result": result},
    )
