from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_domain_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/domains/{domain}/hotlink-protection", tags=["hotlink"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/domains/{domain}/hotlink-protection", tags=["ui:hotlink"])


class HotlinkProtectionBody(BaseModel):
    enabled: bool = True
    allowed_domains: list[str] = []


@api_router.get("")
def get_hotlink_protection(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("hotlink.get", identity, domain=domain)


@api_router.patch("")
def set_hotlink_protection(username: str, domain: str, body: HotlinkProtectionBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("hotlink.set", identity, domain=domain, **body.model_dump())


@ui_router.get("")
def ui_hotlink_home(request: Request, username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    current = call_daemon("hotlink.get", identity, domain=domain)
    return templates.TemplateResponse(
        request, "hotlink_protection.html", {"identity": identity, "username": username, "domain": domain, "hotlink": current}
    )


@ui_router.post("")
def ui_set_hotlink_protection(
    username: str,
    domain: str,
    enabled: str = Form(""),
    allowed_domains: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    domains_list = [d.strip() for d in allowed_domains.splitlines() if d.strip()]
    call_daemon("hotlink.set", identity, domain=domain, enabled=bool(enabled), allowed_domains=domains_list)
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/hotlink-protection", status_code=303)
