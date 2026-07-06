"""Phase 7b feature 6: one-click staging clone of a domain."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_domain_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/domains/{domain}/staging", tags=["staging"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/domains/{domain}/staging", tags=["ui:staging"])


@api_router.post("")
def create_staging(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("staging.create", identity, username=username, domain=domain)


@api_router.get("")
def get_staging(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("staging.get", identity, username=username, domain=domain)


@api_router.post("/sync")
def sync_staging(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("staging.sync", identity, username=username, domain=domain)


@api_router.delete("")
def delete_staging(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("staging.delete", identity, username=username, domain=domain)


@ui_router.get("")
def ui_staging_home(request: Request, username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    data = call_daemon("staging.get", identity, username=username, domain=domain)
    return templates.TemplateResponse(request, "staging.html", {"identity": identity, "username": username, "domain": domain, "data": data})


@ui_router.post("")
def ui_create_staging(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    call_daemon("staging.create", identity, username=username, domain=domain)
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/staging", status_code=303)


@ui_router.post("/sync")
def ui_sync_staging(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    call_daemon("staging.sync", identity, username=username, domain=domain)
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/staging", status_code=303)


@ui_router.post("/delete")
def ui_delete_staging(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    call_daemon("staging.delete", identity, username=username, domain=domain)
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/staging", status_code=303)
