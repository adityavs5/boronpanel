from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/domains/{domain}/redirects", tags=["redirects"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/domains/{domain}/redirects", tags=["ui:redirects"])


class RedirectBody(BaseModel):
    path: str
    target_url: str
    status_code: int = 301


@api_router.get("")
def list_redirects(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("redirect.list", identity, domain=domain)


@api_router.post("")
def create_redirect(username: str, domain: str, body: RedirectBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("redirect.create", identity, domain=domain, **body.model_dump())


@api_router.put("")
def update_redirect(username: str, domain: str, body: RedirectBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("redirect.update", identity, domain=domain, **body.model_dump())


@api_router.delete("")
def delete_redirect(username: str, domain: str, path: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("redirect.delete", identity, domain=domain, path=path)


@ui_router.get("")
def ui_redirects_home(request: Request, username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    redirects = call_daemon("redirect.list", identity, domain=domain)["redirects"]
    return templates.TemplateResponse(
        request, "redirects.html", {"identity": identity, "username": username, "domain": domain, "redirects": redirects}
    )


@ui_router.post("")
def ui_create_redirect(
    username: str,
    domain: str,
    path: str = Form(...),
    target_url: str = Form(...),
    status_code: int = Form(301),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    call_daemon("redirect.create", identity, domain=domain, path=path, target_url=target_url, status_code=status_code)
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/redirects", status_code=303)


@ui_router.post("/edit")
def ui_update_redirect(
    username: str,
    domain: str,
    path: str = Form(...),
    target_url: str = Form(...),
    status_code: int = Form(301),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    call_daemon("redirect.update", identity, domain=domain, path=path, target_url=target_url, status_code=status_code)
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/redirects", status_code=303)


@ui_router.post("/delete")
def ui_delete_redirect(username: str, domain: str, path: str = Form(...), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("redirect.delete", identity, domain=domain, path=path)
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/redirects", status_code=303)
