"""Phase 7b feature 5: account usage alerts (80/90/100% of disk/bandwidth/
databases/email accounts/subdomains).

Route naming note: the goal's own text names `PATCH /accounts/{u}/limits`
for this feature -- but that exact path is already `account.set_limits`
(cpu/mem/io/pids cgroup limits, Phase 2 feature 6, api/routers/accounts.py),
shipped and live long before this feature existed. Reusing it here would
silently overload one endpoint with two unrelated meanings depending on
which body fields happen to be present -- a real correctness/discoverability
regression for an already-shipped route, not a private implementation
detail free to change. `/usage-limits` is used instead so both endpoints
keep a single, unambiguous meaning; `GET /accounts/{u}/alerts` matches the
goal's own text exactly, since no prior route claims that path.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_admin
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/usage-limits", tags=["usage-alerts"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/usage-limits", tags=["ui:usage-alerts"])
alerts_api_router = APIRouter(prefix="/api/v1/accounts/{username}/alerts", tags=["usage-alerts"])
alerts_ui_router = APIRouter(prefix="/ui/accounts/{username}/alerts", tags=["ui:usage-alerts"])


class SetLimitsBody(BaseModel):
    bandwidth_limit_mb: int | None = None
    database_limit: int | None = None
    email_account_limit: int | None = None
    subdomain_limit: int | None = None
    auto_suspend_at_100: bool | None = None


@api_router.get("")
def get_limits(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("usage.limits.get", identity, username=username)


@api_router.patch("")
def set_limits(username: str, body: SetLimitsBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)  # goal: "Admin sets limits per account."
    return call_daemon("usage.limits.set", identity, username=username, **body.model_dump(exclude_unset=True))


@alerts_api_router.get("")
def get_alerts(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("usage.alerts.get", identity, username=username)


@ui_router.get("")
def ui_limits(request: Request, username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    limits = call_daemon("usage.limits.get", identity, username=username)
    return templates.TemplateResponse(
        request, "usage_limits.html", {"identity": identity, "username": username, "limits": limits}
    )


@ui_router.post("")
def ui_set_limits(
    username: str,
    bandwidth_limit_mb: str = Form(""),
    database_limit: str = Form(""),
    email_account_limit: str = Form(""),
    subdomain_limit: str = Form(""),
    auto_suspend_at_100: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    call_daemon(
        "usage.limits.set", identity, username=username,
        bandwidth_limit_mb=int(bandwidth_limit_mb) if bandwidth_limit_mb else None,
        database_limit=int(database_limit) if database_limit else None,
        email_account_limit=int(email_account_limit) if email_account_limit else None,
        subdomain_limit=int(subdomain_limit) if subdomain_limit else None,
        auto_suspend_at_100=bool(auto_suspend_at_100),
    )
    return RedirectResponse(f"/ui/accounts/{username}/usage-limits", status_code=303)


@alerts_ui_router.get("")
def ui_alerts(request: Request, username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    data = call_daemon("usage.alerts.get", identity, username=username)
    return templates.TemplateResponse(request, "usage_alerts.html", {"identity": identity, "username": username, "data": data})
