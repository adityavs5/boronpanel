from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_domain_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/domains/{domain}/nameservers", tags=["nameservers"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/domains/{domain}/nameservers", tags=["ui:nameservers"])


class SetNameserversBody(BaseModel):
    nameservers: list[str]
    glue: dict[str, str] = {}


@api_router.get("")
def list_nameservers(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("nameservers.list", identity, domain=domain)


@api_router.put("")
def set_nameservers(username: str, domain: str, body: SetNameserversBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("nameservers.set", identity, domain=domain, nameservers=body.nameservers, glue=body.glue)


@api_router.delete("")
def reset_nameservers(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("nameservers.reset", identity, domain=domain)


@ui_router.get("")
def ui_nameservers_home(request: Request, username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    data = call_daemon("nameservers.list", identity, domain=domain)
    return templates.TemplateResponse(
        request, "nameservers.html",
        {"identity": identity, "username": username, "domain": domain, "nameservers": data["nameservers"], "glue": data["glue"]},
    )


@ui_router.post("")
def ui_set_nameservers(
    username: str,
    domain: str,
    ns_list: str = Form(...),
    glue_ips: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    nameservers = [ns.strip() for ns in ns_list.splitlines() if ns.strip()]
    glue = {}
    for line in glue_ips.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        host, ip = line.split("=", 1)
        glue[host.strip()] = ip.strip()
    call_daemon("nameservers.set", identity, domain=domain, nameservers=nameservers, glue=glue)
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/nameservers", status_code=303)


@ui_router.post("/reset")
def ui_reset_nameservers(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    call_daemon("nameservers.reset", identity, domain=domain)
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/nameservers", status_code=303)
