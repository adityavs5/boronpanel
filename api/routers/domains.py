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
    parent_domain: str | None = None
    document_root_mode: str = "default"
    document_root: str | None = None


class SetDomainPhpVersionBody(BaseModel):
    php_version: str | None = None


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


@api_router.patch("/{domain}/php-version")
def set_domain_php_version(username: str, domain: str, body: SetDomainPhpVersionBody, identity: Identity = Depends(get_identity)):
    # Self-service, not admin-only -- same posture as
    # accounts.py's account-level set_php_version.
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("domain.set_php_version", identity, username=username, domain=domain, php_version=body.php_version)


@ui_router.post("")
def ui_add_domain(
    username: str,
    domain: str = Form(...),
    kind: str = Form("addon"),
    parent_domain: str = Form(""),
    document_root_mode: str = Form("default"),
    document_root: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    call_daemon("domain.add", identity, username=username, domain=domain, kind=kind,
                parent_domain=parent_domain or None, document_root_mode=document_root_mode,
                document_root=document_root or None)
    return RedirectResponse(f"/ui/accounts/{username}", status_code=303)


@ui_router.post("/{domain}/remove")
def ui_remove_domain(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    call_daemon("domain.remove", identity, username=username, domain=domain)
    return RedirectResponse(f"/ui/accounts/{username}", status_code=303)


@ui_router.post("/{domain}/php-version")
def ui_set_domain_php_version(username: str, domain: str, php_version: str = Form(""), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    call_daemon("domain.set_php_version", identity, username=username, domain=domain, php_version=php_version or None)
    return RedirectResponse(f"/ui/accounts/{username}", status_code=303)
