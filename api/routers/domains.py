from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_domain_access

api_router = APIRouter(prefix="/api/v1/accounts/{username}/domains", tags=["domains"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/domains", tags=["ui:domains"])


class AddDomainBody(BaseModel):
    domain: str
    kind: str = "addon"


@api_router.get("")
def list_domains(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("domain.list", identity, username=username)


@api_router.post("")
def add_domain(username: str, body: AddDomainBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("domain.add", identity, username=username, **body.model_dump())


@api_router.delete("/{domain}")
def remove_domain(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("domain.remove", identity, username=username, domain=domain)


@ui_router.post("")
def ui_add_domain(
    username: str,
    domain: str = Form(...),
    kind: str = Form("addon"),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    call_daemon("domain.add", identity, username=username, domain=domain, kind=kind)
    return RedirectResponse(f"/ui/accounts/{username}", status_code=303)


@ui_router.post("/{domain}/remove")
def ui_remove_domain(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    call_daemon("domain.remove", identity, username=username, domain=domain)
    return RedirectResponse(f"/ui/accounts/{username}", status_code=303)
