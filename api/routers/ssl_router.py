from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_admin, require_domain_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/ssl", tags=["ssl"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/ssl", tags=["ui:ssl"])

# Phase 3 feature 8: the goal's literal API shape is account-scoped
# (/accounts/{u}/ssl, /accounts/{u}/domains/{d}/ssl/issue) -- added
# alongside the existing domain-scoped routes above (kept as-is, still
# used elsewhere) rather than replacing them.
account_api_router = APIRouter(prefix="/api/v1/accounts/{username}", tags=["ssl"])
admin_router = APIRouter(prefix="/api/v1/admin/ssl", tags=["ssl-admin"])


class IssueCertBody(BaseModel):
    domain: str


class DomainIssueCertBody(BaseModel):
    force: bool = False


@api_router.post("/issue")
def issue_certificate(body: IssueCertBody, identity: Identity = Depends(get_identity)):
    require_domain_access(identity, body.domain)
    return call_daemon("ssl.issue", identity, domain=body.domain)


@api_router.get("/{domain}/status")
def certificate_status(domain: str, identity: Identity = Depends(get_identity)):
    require_domain_access(identity, domain)
    return call_daemon("ssl.status", identity, domain=domain)


@account_api_router.get("/ssl")
def ssl_dashboard(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("ssl.dashboard", identity, username=username)


@account_api_router.post("/domains/{domain}/ssl/issue")
def issue_or_renew_certificate(username: str, domain: str, body: DomainIssueCertBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("ssl.issue", identity, domain=domain, force=body.force)


@account_api_router.post("/domains/{domain}/ssl/wildcard")
def issue_wildcard_certificate(username: str, domain: str, body: DomainIssueCertBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("ssl.issue_wildcard", identity, domain=domain, force=body.force)


@admin_router.get("")
def admin_ssl_dashboard(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("ssl.admin.dashboard", identity)


@admin_router.post("/domains/{domain}/issue")
def admin_issue_certificate(domain: str, body: DomainIssueCertBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("ssl.issue", identity, domain=domain, force=body.force)


@admin_router.post("/domains/{domain}/wildcard")
def admin_issue_wildcard_certificate(domain: str, body: DomainIssueCertBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("ssl.issue_wildcard", identity, domain=domain, force=body.force)


@ui_router.get("")
def ui_ssl_dashboard(request: Request, username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    dashboard = call_daemon("ssl.dashboard", identity, username=username)
    return templates.TemplateResponse(
        request, "ssl_dashboard.html", {"identity": identity, "username": username, "dashboard": dashboard}
    )


@ui_router.post("/{domain}/issue")
def ui_issue_certificate(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    call_daemon("ssl.issue", identity, domain=domain)
    return RedirectResponse(f"/ui/accounts/{username}/ssl", status_code=303)


@ui_router.post("/{domain}/renew")
def ui_renew_certificate(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    call_daemon("ssl.issue", identity, domain=domain, force=True)
    return RedirectResponse(f"/ui/accounts/{username}/ssl", status_code=303)


@ui_router.post("/{domain}/wildcard")
def ui_issue_wildcard_certificate(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    call_daemon("ssl.issue_wildcard", identity, domain=domain)
    return RedirectResponse(f"/ui/accounts/{username}/ssl", status_code=303)
