from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_domain_access, require_admin
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/domains/{domain}/wordpress", tags=["wordpress"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/domains/{domain}/wordpress", tags=["ui:wordpress"])


class InstallWordPressBody(BaseModel):
    path: str = ""  # QA round 2, item 3: install into a subdirectory (e.g. "blog") instead of the docroot itself
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


# --- QA round 2, item 2: per-domain WordPress management (WP-CLI actions,
# reusing the existing daemon/wpcli.py backend built for the account-level
# DevTools tab -- api/routers/devtools.py, unchanged). item 3: every
# endpoint below is (domain, path)-scoped, so a domain with multiple
# installs (root + subdirectories) manages each independently. ---------


@api_router.get("/installs")
def list_domain_installs(username: str, domain: str, identity: Identity = Depends(get_identity)):
    """Live-detected WordPress installs at this domain (root + one level
    of subdirectories) -- this is what makes the management section
    "show once WP detected," independent of whether the install was made
    via Boron's own one-click installer or uploaded/imported another way."""
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("wpcli.detect", identity, username=username, domain=domain)


class WpcliActionBody(BaseModel):
    path: str = ""  # which install at this domain -- "" for the docroot itself, else a subdirectory
    action: str
    # optional per-action fields, mirroring api/routers/devtools.py's WpcliBody
    name: str | None = None
    all: bool = False
    user: str | None = None
    search: str | None = None
    replace: str | None = None
    preview: bool = True


@api_router.post("/actions")
def run_wpcli_action(username: str, domain: str, body: WpcliActionBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("wpcli.run", identity, username=username, domain=domain, **body.model_dump(exclude_none=True))


@api_router.get("/actions/runs")
def list_wpcli_action_runs(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("wpcli.list", identity, username=username)


@api_router.get("/actions/runs/{job_id}")
def get_wpcli_action_run(username: str, domain: str, job_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("wpcli.get", identity, username=username, job_id=job_id)


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


manager_router = APIRouter(prefix="/api/v1/wordpress", tags=["wordpress-manager"])

@manager_router.get("")
def manager_inventory(username: str | None = None, identity: Identity = Depends(get_identity)):
    if username: require_account_access(identity, username)
    else: require_admin(identity)
    return call_daemon("wpmanager.inventory", identity, username=username)

class ManagerBody(BaseModel):
    path: str = ""
    action: str
    backup: str = ""
    target_domain: str = ""
    target_path: str = ""
    confirm: bool = False

@api_router.post("/manage")
def manage_site(username: str, domain: str, body: ManagerBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("wpmanager.operation", identity, username=username, domain=domain, **body.model_dump())

class LoginBody(BaseModel):
    path: str = ""

@api_router.post("/login")
def login_site(username: str, domain: str, body: LoginBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("wpmanager.login", identity, username=username, domain=domain, path=body.path)


@api_router.post("/login/open", response_class=HTMLResponse)
def open_wordpress_login(username: str, domain: str, path: str = Form(""), identity: Identity = Depends(get_identity)):
    """Same-origin POST handoff with a CSP scoped to this verified site's URL."""
    import html
    import secrets
    from urllib.parse import urlsplit
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    link = call_daemon("wpmanager.login", identity, username=username, domain=domain, path=path)
    nonce = secrets.token_urlsafe(24)
    origin = urlsplit(link['url'])
    destination = f"{origin.scheme}://{origin.netloc}"
    document = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Opening WordPress</title></head>
<body><p>Opening your WordPress dashboard…</p>
<form id="login" action="{html.escape(link['url'], quote=True)}" method="post">
<input type="hidden" name="token" value="{html.escape(link['token'], quote=True)}"><button type="submit">Continue to WordPress</button></form>
<script nonce="{nonce}">document.getElementById('login').submit()</script></body></html>"""
    return HTMLResponse(document, headers={
        'Cache-Control': 'no-store',
        'Content-Security-Policy': f"default-src 'none'; script-src 'nonce-{nonce}'; form-action {destination}; base-uri 'none'; frame-ancestors 'none'",
        'Referrer-Policy': 'no-referrer',
    })


class ScanBody(BaseModel):
    username: str | None = None


@manager_router.post('/scan')
def scan_installations(body: ScanBody, identity: Identity = Depends(get_identity)):
    if body.username: require_account_access(identity, body.username)
    else: require_admin(identity)
    return call_daemon('wpmanager.scan', identity, username=body.username)


@api_router.post('/refresh')
def refresh_installation(username: str, domain: str, body: LoginBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon('wpmanager.refresh', identity, username=username, domain=domain, path=body.path)


class RemoveBody(BaseModel):
    path: str = ''
    mode: str
    confirmation: str = ''


@api_router.post('/remove')
def remove_installation(username: str, domain: str, body: RemoveBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon('wpmanager.remove', identity, username=username, domain=domain, **body.model_dump())
