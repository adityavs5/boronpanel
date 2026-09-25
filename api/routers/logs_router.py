from __future__ import annotations

import base64

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/logs", tags=["logs"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/logs", tags=["ui:logs"])


@api_router.get("/{type}")
def get_log(username: str, type: str, domain: str | None = None, severity: str | None = None,
            segment: str | None = None, from_date: str | None = None, to_date: str | None = None,
            lines: int = 500, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    params = {"username": username, "type": type}
    if domain:
        params["domain"] = domain
    if severity:
        params["severity"] = severity
    if segment:
        params["segment"] = segment
    if from_date:
        params["from_date"] = from_date
    if to_date:
        params["to_date"] = to_date
    params["lines"] = lines
    return call_daemon("logs.get", identity, **params)


@api_router.get("/{type}/download")
def download_log(username: str, type: str, domain: str | None = None,
                 segment: str | None = None, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    params = {"username": username, "type": type}
    if domain:
        params["domain"] = domain
    if segment:
        params["segment"] = segment
    result = call_daemon("logs.download", identity, **params)
    filename = result["filename"].replace('"', '')
    return Response(
        base64.b64decode(result["content_base64"], validate=True),
        media_type=result["content_type"],
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
    )


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
