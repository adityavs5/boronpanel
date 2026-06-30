from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_domain_access

api_router = APIRouter(prefix="/api/v1/ssl", tags=["ssl"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/ssl", tags=["ui:ssl"])


class IssueCertBody(BaseModel):
    domain: str


@api_router.post("/issue")
def issue_certificate(body: IssueCertBody, identity: Identity = Depends(get_identity)):
    require_domain_access(identity, body.domain)
    return call_daemon("ssl.issue", identity, domain=body.domain)


@api_router.get("/{domain}/status")
def certificate_status(domain: str, identity: Identity = Depends(get_identity)):
    require_domain_access(identity, domain)
    return call_daemon("ssl.status", identity, domain=domain)


@ui_router.post("/{domain}/issue")
def ui_issue_certificate(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("ssl.issue", identity, domain=domain)
    return RedirectResponse(f"/ui/accounts/{username}", status_code=303)
