from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from starlette.requests import Request

from shared.config import settings
from shared.db import read_session
from shared.models import Account

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_admin
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts", tags=["accounts"])
ui_router = APIRouter(prefix="/ui/accounts", tags=["ui:accounts"])


class CreateAccountBody(BaseModel):
    username: str
    primary_domain: str | None = None
    php_version: str | None = None
    quota_soft_mb: int | None = None
    quota_hard_mb: int | None = None
    password: str | None = None


@api_router.post("")
def create_account(body: CreateAccountBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("account.create", identity, **body.model_dump(exclude_none=True))


@api_router.get("")
def list_accounts(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    with read_session() as db:
        accounts = db.scalars(select(Account).order_by(Account.username)).all()
        return [
            {"id": a.id, "username": a.username, "status": a.status, "primary_domain": a.primary_domain}
            for a in accounts
        ]


@api_router.get("/{username}")
def get_account(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("account.get", identity, username=username)


@api_router.post("/{username}/suspend")
def suspend_account(username: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("account.suspend", identity, username=username)


@api_router.post("/{username}/unsuspend")
def unsuspend_account(username: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("account.unsuspend", identity, username=username)


@api_router.post("/{username}/terminate")
def terminate_account(username: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("account.terminate", identity, username=username)


class SetPhpVersionBody(BaseModel):
    php_version: str


@api_router.patch("/{username}/php-version")
def set_php_version(username: str, body: SetPhpVersionBody, identity: Identity = Depends(get_identity)):
    # Self-service, not admin-only: PHP version is a per-account convenience
    # setting a customer manages themselves in cPanel-equivalent panels,
    # unlike suspend/terminate which are operator actions.
    require_account_access(identity, username)
    return call_daemon("account.set_php_version", identity, username=username, php_version=body.php_version)


class SetLimitsBody(BaseModel):
    cpu_pct: int
    mem_mb: int
    io_mb: int
    pids_max: int


@api_router.patch("/{username}/limits")
def set_limits(username: str, body: SetLimitsBody, identity: Identity = Depends(get_identity)):
    # Admin-only, unlike php-version: a customer raising their own resource
    # limits would defeat the point of having them.
    require_admin(identity)
    return call_daemon("account.set_limits", identity, username=username, **body.model_dump())


class SetNamespaceBody(BaseModel):
    enabled: bool


@api_router.get("/{username}/namespace")
def get_namespace_status(username: str, identity: Identity = Depends(get_identity)):
    # Readable by the account owner too (like php-version, unlike the
    # write side below) -- a customer can see their own isolation posture,
    # they just can't change it themselves.
    require_account_access(identity, username)
    return call_daemon("namespace.status", identity, username=username)


@api_router.patch("/{username}/namespace")
def set_namespace(username: str, body: SetNamespaceBody, identity: Identity = Depends(get_identity)):
    # Admin-only, same category as limits: a security/infra posture
    # decision, not a customer self-service convenience.
    require_admin(identity)
    op = "namespace.enable" if body.enabled else "namespace.disable"
    return call_daemon(op, identity, username=username)


@api_router.post("/namespace/bulk-enable")
def trigger_namespace_bulk_enable(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("namespace.bulk_enable.trigger", identity)


@api_router.get("/namespace/bulk-enable/{job_id}")
def get_namespace_bulk_enable(job_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("namespace.bulk_enable.get", identity, job_id=job_id)


# --- server-rendered UI (ARCHITECTURE.md SS1: Jinja2 + htmx, forms POST-Redirect-GET) ---


@ui_router.get("")
def ui_dashboard(request: Request, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    with read_session() as db:
        accounts = db.scalars(select(Account).order_by(Account.username)).all()
    return templates.TemplateResponse(request, "dashboard.html", {"identity": identity, "accounts": accounts})


@ui_router.post("")
def ui_create_account(
    request: Request,
    username: str = Form(...),
    primary_domain: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    call_daemon(
        "account.create",
        identity,
        username=username,
        **({"primary_domain": primary_domain} if primary_domain else {}),
    )
    return RedirectResponse(f"/ui/accounts/{username}", status_code=303)


@ui_router.get("/{username}")
def ui_account_detail(request: Request, username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    account = call_daemon("account.get", identity, username=username)
    domains = call_daemon("domain.list", identity, username=username)["domains"]
    databases = call_daemon("db.list", identity, username=username)["databases"]
    # db_name is "<username>_<suffix>" (daemon/handlers_database.py); the UI
    # delete route takes just the suffix, so strip the prefix here in code
    # rather than string-slicing inside the Jinja template.
    for db in databases:
        db["suffix"] = db["db_name"][len(username) + 1 :]
    # force_refresh=False: this page loads often (every visit to the
    # account), so it must ride the 15-min cache rather than trigger a du
    # scan on every click -- the dedicated usage page has its own explicit
    # "refresh now" link for that.
    usage = call_daemon("usage.get", identity, username=username, force_refresh=False)
    php_ini_result = call_daemon("php_ini.get", identity, username=username)
    namespace_status = call_daemon("namespace.status", identity, username=username)
    return templates.TemplateResponse(
        request,
        "account_detail.html",
        {
            "identity": identity,
            "account": account,
            "domains": domains,
            "databases": databases,
            "php_versions": settings.php_versions,
            "usage": usage,
            "php_ini": php_ini_result["php_ini"] or php_ini_result["defaults"],
            "php_ini_is_custom": php_ini_result["php_ini"] is not None,
            "namespace_status": namespace_status,
        },
    )


@ui_router.post("/{username}/suspend")
def ui_suspend(username: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("account.suspend", identity, username=username)
    return RedirectResponse(f"/ui/accounts/{username}", status_code=303)


@ui_router.post("/{username}/unsuspend")
def ui_unsuspend(username: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("account.unsuspend", identity, username=username)
    return RedirectResponse(f"/ui/accounts/{username}", status_code=303)


@ui_router.post("/{username}/terminate")
def ui_terminate(username: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("account.terminate", identity, username=username)
    return RedirectResponse("/ui/accounts", status_code=303)


@ui_router.post("/{username}/php-version")
def ui_set_php_version(username: str, php_version: str = Form(...), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("account.set_php_version", identity, username=username, php_version=php_version)
    return RedirectResponse(f"/ui/accounts/{username}", status_code=303)


@ui_router.post("/{username}/limits")
def ui_set_limits(
    username: str,
    cpu_pct: int = Form(...),
    mem_mb: int = Form(...),
    io_mb: int = Form(...),
    pids_max: int = Form(...),
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    call_daemon("account.set_limits", identity, username=username, cpu_pct=cpu_pct, mem_mb=mem_mb, io_mb=io_mb, pids_max=pids_max)
    return RedirectResponse(f"/ui/accounts/{username}", status_code=303)


@ui_router.post("/{username}/namespace")
def ui_set_namespace(username: str, enabled: str = Form(...), identity: Identity = Depends(get_identity)):
    require_admin(identity)
    op = "namespace.enable" if enabled == "1" else "namespace.disable"
    call_daemon(op, identity, username=username)
    return RedirectResponse(f"/ui/accounts/{username}", status_code=303)


@ui_router.get("/namespace/bulk-enable")
def ui_namespace_bulk_enable(request: Request, job_id: int | None = None, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    job = call_daemon("namespace.bulk_enable.get", identity, job_id=job_id) if job_id else None
    return templates.TemplateResponse(request, "namespace_bulk_enable.html", {"identity": identity, "job": job})


@ui_router.post("/namespace/bulk-enable")
def ui_trigger_namespace_bulk_enable(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    job = call_daemon("namespace.bulk_enable.trigger", identity)
    return RedirectResponse(f"/ui/accounts/namespace/bulk-enable?job_id={job['id']}", status_code=303)
