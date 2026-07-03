from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_domain_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/domains/{domain}/wordpress", tags=["wordpress"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/domains/{domain}/wordpress", tags=["ui:wordpress"])


class InstallWordPressBody(BaseModel):
    title: str | None = None
    admin_user: str | None = None
    admin_email: str | None = None
    admin_password: str | None = None


@api_router.post("")
def trigger_install(username: str, domain: str, body: InstallWordPressBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("wordpress.install.trigger", identity, username=username, domain=domain, **body.model_dump(exclude_none=True))


@api_router.get("/jobs/{job_id}")
def get_job(username: str, domain: str, job_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("wordpress.install.get", identity, username=username, job_id=job_id)


@ui_router.get("")
def ui_wordpress_home(request: Request, username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    installs = call_daemon("wordpress.install.list", identity, username=username)["installs"]
    existing = next((i for i in installs if i["domain"] == domain), None)
    return templates.TemplateResponse(
        request, "wordpress_install.html", {"identity": identity, "username": username, "domain": domain, "existing": existing, "job": None}
    )


@ui_router.post("")
def ui_trigger_install(
    username: str,
    domain: str,
    title: str = Form(""),
    admin_user: str = Form("admin"),
    admin_email: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    params = {"username": username, "domain": domain}
    if title:
        params["title"] = title
    if admin_user:
        params["admin_user"] = admin_user
    if admin_email:
        params["admin_email"] = admin_email
    result = call_daemon("wordpress.install.trigger", identity, **params)
    return RedirectResponse(
        f"/ui/accounts/{username}/domains/{domain}/wordpress/jobs/{result['id']}", status_code=303
    )


@ui_router.get("/jobs/{job_id}")
def ui_job_status(request: Request, username: str, domain: str, job_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    job = call_daemon("wordpress.install.get", identity, username=username, job_id=job_id)
    return templates.TemplateResponse(
        request, "wordpress_install.html", {"identity": identity, "username": username, "domain": domain, "existing": None, "job": job}
    )
