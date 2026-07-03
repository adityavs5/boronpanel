from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_domain_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}", tags=["apps"])
domain_api_router = APIRouter(prefix="/api/v1/accounts/{username}/domains/{domain}/apps", tags=["apps"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/domains/{domain}/apps", tags=["ui:apps"])

APP_CHOICES = [
    ("wordpress", "WordPress"),
    ("joomla", "Joomla"),
    ("drupal", "Drupal"),
    ("prestashop", "PrestaShop"),
    ("laravel", "Laravel (skeleton)"),
    ("static", "Static HTML"),
]


class InstallAppBody(BaseModel):
    title: str | None = None
    admin_user: str | None = None
    admin_email: str | None = None
    admin_password: str | None = None


@api_router.get("/apps")
def list_installed_apps(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.list", identity, username=username)


@domain_api_router.post("/{app_id}")
def trigger_install(username: str, domain: str, app_id: str, body: InstallAppBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("apps.install.trigger", identity, username=username, domain=domain, app_id=app_id, **body.model_dump(exclude_none=True))


@domain_api_router.get("/jobs/{job_id}")
def get_job(username: str, domain: str, job_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("apps.install.get", identity, username=username, job_id=job_id)


# --- UI ---------------------------------------------------------------------


@ui_router.get("")
def ui_apps_home(request: Request, username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    installed = call_daemon("apps.list", identity, username=username)["apps"]
    existing = next((a for a in installed if a["domain"] == domain), None)
    return templates.TemplateResponse(
        request, "apps_install.html",
        {"identity": identity, "username": username, "domain": domain, "existing": existing, "job": None, "app_choices": APP_CHOICES},
    )


@ui_router.post("")
def ui_trigger_install(
    username: str,
    domain: str,
    app_id: str = Form(...),
    title: str = Form(""),
    admin_user: str = Form("admin"),
    admin_email: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    params = {"username": username, "domain": domain, "app_id": app_id}
    if title:
        params["title"] = title
    if admin_user:
        params["admin_user"] = admin_user
    if admin_email:
        params["admin_email"] = admin_email
    result = call_daemon("apps.install.trigger", identity, **params)
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/apps/jobs/{result['id']}", status_code=303)


@ui_router.get("/jobs/{job_id}")
def ui_job_status(request: Request, username: str, domain: str, job_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    job = call_daemon("apps.install.get", identity, username=username, job_id=job_id)
    return templates.TemplateResponse(
        request, "apps_install.html",
        {"identity": identity, "username": username, "domain": domain, "existing": None, "job": job, "app_choices": APP_CHOICES},
    )
