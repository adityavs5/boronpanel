from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_domain_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/domains/{domain}/ip-block", tags=["ipblock"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/domains/{domain}/ip-block", tags=["ui:ipblock"])


class IpBlockBody(BaseModel):
    entry: str


@api_router.get("")
def list_ip_blocks(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("ipblock.list", identity, domain=domain)


@api_router.post("")
def add_ip_block(username: str, domain: str, body: IpBlockBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("ipblock.add", identity, domain=domain, entry=body.entry)


@api_router.delete("")
def remove_ip_block(username: str, domain: str, entry: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("ipblock.remove", identity, domain=domain, entry=entry)


@ui_router.get("")
def ui_ip_block_home(request: Request, username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    current = call_daemon("ipblock.list", identity, domain=domain)
    return templates.TemplateResponse(
        request, "ip_block.html", {"identity": identity, "username": username, "domain": domain, "blocked": current["blocked"]}
    )


@ui_router.post("")
def ui_add_ip_block(username: str, domain: str, entry: str = Form(...), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    call_daemon("ipblock.add", identity, domain=domain, entry=entry)
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/ip-block", status_code=303)


@ui_router.post("/delete")
def ui_remove_ip_block(username: str, domain: str, entry: str = Form(...), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    call_daemon("ipblock.remove", identity, domain=domain, entry=entry)
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/ip-block", status_code=303)
