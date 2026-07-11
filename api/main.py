"""forgehost-api: the unprivileged REST API + admin UI process.

ARCHITECTURE.md SS2: runs as the `forgehost-api` system user, never root,
talks to forgehostd only over the Unix socket RPC (api/rpc.py). Every
state-changing endpoint is reached through the same get_identity/
require_*_access dependencies regardless of HTTP verb (SS9).
"""
from __future__ import annotations

import math
import time

from pathlib import Path

import ipaddress

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from shared.config import require_secure_session_secret, settings
from shared.db import read_session
from shared.models import IpWhitelistEntry

from api import logsetup, ratelimit
from api.security import Identity, get_identity, require_admin
from api.routers import account_backups, accounts, adminlogs, apps, auditlog, auth, backups, bandwidth, branding, bulkops, cloudflare, cpanel_import, cron, databases, dbmonitor, devtools, disktree, dns, domains, email, email_extras, errorpages, fail2ban, fileauth, filebrowser, firewall, forwarding, ftp, git, health, hotlink, identity_admin, imapsync, impersonation, ipblock, ipwhitelist, logs_router, lscache_router, mail, mailqueue, maintenance, monitoring, nameservers, nodeapps, notes, notifications, onboarding, parked, php_ini, plans, pma, processes, pythonapps, redirects, redis_router, services, sitestats, slowquery, spamfilter, sshkeys, ssl_router, staging, terminal, tokens, twofactor, update, usage, usage_alerts, waf, webhooks, wildcard, wordpress


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Refuse to serve with a missing/insecure session-signing key -- see
    # shared.config.require_secure_session_secret. A hard boot failure here is
    # the whole point: a forgeable session cookie is a full auth bypass.
    require_secure_session_secret()
    # Run A feature 7: open the request-log file handlers up front (the
    # middleware would lazily configure on first request anyway, but doing
    # it here surfaces an unwritable log dir at boot, in the log, rather
    # than silently on the first hit).
    logsetup.configure()
    yield


# Run A feature 8: API documentation. The built-in docs are DISABLED here
# (docs_url/redoc_url/openapi_url all None) and re-exposed below behind an
# admin-session gate -- the raw OpenAPI schema names every internal path and
# is operator information, not something to serve to the unauthenticated
# world. `_OPENAPI_TAGS` gives the interactive docs their section
# descriptions; per-endpoint descriptions come from each handler's own
# docstring/summary (FastAPI reads them automatically).
_OPENAPI_DESCRIPTION = """
Forgehost is a single-server Linux hosting control panel. This is the REST
API that both the React panel and any external integration (e.g. a billing
system) drive -- every panel action is one of these calls.

**Authentication.** Two mechanisms, same role model (`admin` /
`customer`):
* **Session cookie** (`fh_session`) -- how the browser panel authenticates,
  issued by `POST /login`.
* **Bearer token** (`Authorization: Bearer fh_<role>_<...>`) -- for
  machine-to-machine use; issue/revoke under **API Tokens**. A token can be
  scoped to a single account.

Admins reach every account; a customer identity is scoped to exactly one
account, enforced server-side by username (never a client-supplied id).

**These docs require an admin session** -- open them while signed in to the
panel as an admin in the same browser.
"""

_OPENAPI_TAGS = [
    {"name": "auth", "description": "Login, 2FA, logout, self password change."},
    {"name": "accounts", "description": "Hosting account lifecycle: create, suspend/unsuspend, terminate, resource limits."},
    {"name": "plans", "description": "Named limit presets (Run A f1) and applying them to accounts."},
    {"name": "domains", "description": "Primary/addon domains and subdomains."},
    {"name": "dns", "description": "PowerDNS-backed zones and records; Cloudflare zone integration."},
    {"name": "databases", "description": "MariaDB databases and users for hosting accounts."},
    {"name": "mail", "description": "Mailboxes, forwarders, routing, delivery log, mail queue."},
    {"name": "ssl", "description": "Let's Encrypt certificate issuance and status."},
    {"name": "backups", "description": "Account/granular backup + restore, destinations, schedules."},
    {"name": "apps", "description": "One-click apps, WordPress, Node/Python apps, Redis."},
    {"name": "monitoring", "description": "Service health checks and alert settings (Run A f5)."},
    {"name": "admin-logs", "description": "The panel's own recent 5xx request errors (Run A f7)."},
    {"name": "branding", "description": "White-label panel name/logo/favicon (Run A f3)."},
    {"name": "onboarding", "description": "First-login customer onboarding wizard state (Run A f4)."},
]

app = FastAPI(
    title="Forgehost",
    description=_OPENAPI_DESCRIPTION,
    version="1.0",
    openapi_tags=_OPENAPI_TAGS,
    contact={"name": "Forgehost operator", "url": "https://github.com/"},
    license_info={"name": "Proprietary"},
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# Phase 5 feature 9: IP whitelist for panel login. Runs before every
# other route (including /login itself, and every API route -- goal:
# "before login page loads") -- an empty list means no restriction
# (checked first, so the common/default case costs one cheap read and
# nothing else). /healthz is exempt: it's the one route external
# monitoring infrastructure (not an admin browser) is expected to poll,
# and has no state-changing or data-exposing behavior worth restricting.
def ip_allowed(client_host: str | None, whitelist_values: list[str]) -> bool:
    """Pure function (no request/DB objects) so this is directly unit-
    testable without spinning up the ASGI app -- the middleware below is
    just this function plus its I/O."""
    if not whitelist_values:
        return True
    if client_host is None:
        return False
    try:
        client_ip = ipaddress.ip_address(client_host)
    except ValueError:
        return False
    for value in whitelist_values:
        try:
            if "/" in value:
                if client_ip in ipaddress.ip_network(value, strict=False):
                    return True
            elif client_ip == ipaddress.ip_address(value):
                return True
        except ValueError:
            continue
    return False


@app.middleware("http")
async def _ip_whitelist(request, call_next):
    if request.url.path == "/healthz":
        return await call_next(request)
    with read_session() as db:
        values = list(db.scalars(select(IpWhitelistEntry.value)).all())
    client_host = request.client.host if request.client else None
    if not ip_allowed(client_host, values):
        return PlainTextResponse("Forbidden", status_code=403)
    return await call_next(request)


# Run A feature 6: rate limiting (api/ratelimit.py). Starlette runs
# later-registered middleware first, so this sits between the security
# headers (outermost -- 429s still get CSP/HSTS stamped on the way out)
# and the IP whitelist (innermost here -- a rejected flood costs one
# in-memory check per request, never the whitelist's per-request DB read).
@app.middleware("http")
async def _rate_limit(request, call_next):
    decision = ratelimit.evaluate(
        request.url.path,
        request.method,
        request.client.host if request.client else None,
        request.headers.get("cookie"),
        request.headers.get("authorization"),
    )
    if not decision.allowed:
        return PlainTextResponse(
            "Too many requests.\n",
            status_code=429,
            # RFC 6585 / 9110: Retry-After is integer seconds; round up so
            # a client honoring it never retries a hair too early.
            headers={"Retry-After": str(math.ceil(decision.retry_after))},
        )
    return await call_next(request)


# Security audit finding F10: no response ever carried any of these
# headers. Verified safe against the actual UI before adding a real CSP:
# no template anywhere uses an inline <script> tag or loads anything from
# a CDN/external host (this project vendors its one stylesheet, confirmed
# by grep), so `script-src 'none'` doesn't break anything; `style-src
# 'unsafe-inline'` is kept because several templates use inline
# `style="width: N%"` for progress/usage bars.
@app.middleware("http")
async def _security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    # The legacy Jinja UI keeps `script-src 'none'` (audit finding F10 — it has
    # no scripts at all). The React SPA under /app is a bundled, same-origin app
    # so it needs `script-src 'self'` to load its own hashed bundle from
    # /static/dist. It still forbids inline/remote scripts, so the hardening
    # intent (no injected/CDN JS) is preserved; only same-origin bundled JS is
    # permitted, and only for the SPA's own document.
    path = request.url.path
    if path in ("/api/docs", "/api/redoc"):
        # Run A feature 8: Swagger UI / ReDoc bootstrap with a small inline
        # <script> and inline styles, and load their bundle + workers from
        # our own /static/apidocs. Everything is same-origin (no CDN); the
        # relaxation vs. the default `script-src 'none'` is scoped to these
        # two admin-only pages and grants only 'self' + the inline bootstrap.
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
    elif path == "/app" or path.startswith("/app/"):
        # worker-src 'self' blob:  — the Monaco code editor (Phase 8 f13) loads
        # its bundled, same-origin language workers as blob Workers; still no
        # external/CDN script. script-src keeps 'self' (+ 'unsafe-inline' is NOT
        # granted). connect-src 'self' also covers the terminal's same-origin
        # WebSocket (Phase 8 f7).
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; worker-src 'self' blob:; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
    elif path == settings.filebrowser_base_url or path.startswith(settings.filebrowser_base_url + "/"):
        # FileBrowser Quantum's own bundled SPA, served same-origin through the
        # /files proxy. HTML responses get their CSP from the proxy itself
        # (api/routers/filebrowser.html_csp — it hashes the page's own inline
        # bootstrap script, which a static policy here can't allow without
        # breaking on every FB upgrade); don't overwrite it. Non-HTML /files
        # responses get this static fallback: same posture as /app (no
        # inline/remote scripts) plus blob workers + wasm for file previews
        # and data:/blob: media.
        if "content-security-policy" not in response.headers:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; "
                "worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; font-src 'self' data:; "
                "media-src 'self' blob:; connect-src 'self'; "
                "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
            )
    else:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'none'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
    return response


# Run A feature 7: structured request logging. Registered LAST so it is the
# OUTERMOST middleware -- its duration_ms therefore covers the whole stack
# (rate-limit check, whitelist, headers, the route), and it records the
# final status of every response including a rate-limit 429 or a whitelist
# 403 that never reached a route. The `user` is read from request.state,
# which get_identity populated inside the route's dependency (already run
# by the time control returns out here); requests that never authenticated
# simply log user=null.
@app.middleware("http")
async def _access_log(request, call_next):
    start = time.monotonic()
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        # An unhandled exception propagates past the inner ExceptionMiddleware
        # and is turned into a 500 by Starlette's outermost error handler --
        # which sits OUTSIDE this middleware, so without catching here the
        # crash would never reach the error log. Record it as a 500, then
        # re-raise so the real 500 response is still produced upstream.
        _log(request, 500, (time.monotonic() - start) * 1000.0)
        raise
    _log(request, status, (time.monotonic() - start) * 1000.0)
    return response


def _log(request, status: int, duration_ms: float) -> None:
    identity = getattr(request.state, "identity", None)
    logsetup.record_access(
        request.method,
        request.url.path,
        status,
        duration_ms,
        getattr(identity, "username", None),
        request.client.host if request.client else None,
    )

app.include_router(auth.router)
# UI revamp (2026-07-06): the legacy Jinja `ui_router`s are no longer
# registered -- the React SPA at /app is the panel now. Only the JSON
# `api_router`s (and their extra-router-object siblings) are mounted; the SPA
# consumes the same /api/v1/... surface. The ui_router objects still exist in
# each module (harmless dead code) but templates_ui/ has been removed.
for module in (accounts, domains, dns, databases, mail, ssl_router, cron, usage, backups, account_backups, tokens, wordpress, pma, email, ftp, php_ini, redirects, logs_router, hotlink, ipblock, fileauth, git, sshkeys, disktree, nameservers, health, services, mailqueue, firewall, fail2ban, auditlog, waf, slowquery, ipwhitelist, twofactor, nodeapps, pythonapps, redis_router, lscache_router, cpanel_import, bandwidth, webhooks, usage_alerts, staging, cloudflare):
    app.include_router(module.api_router)
# Extra JSON router objects that don't fit the uniform api_router/ui_router
# pair (see each module): account-scoped alerts, admin bandwidth ranking,
# notifications (admin + per-account), apps (list + install), ssl account
# surface, mail password manager, SpamAssassin admin default.
app.include_router(usage_alerts.alerts_api_router)
app.include_router(php_ini.ext_api_router)
app.include_router(bandwidth.admin_api_router)
app.include_router(notifications.admin_api_router)
app.include_router(notifications.api_router)
app.include_router(apps.api_router)
app.include_router(apps.domain_api_router)
app.include_router(ssl_router.account_api_router)
app.include_router(mail.account_api_router)
app.include_router(email.admin_router)
# Phase 8 feature 1: login-as-user. admin_api_router mints the token under
# /api/v1/admin/accounts/{u}/impersonate; api_router redeems/returns.
app.include_router(impersonation.admin_api_router)
app.include_router(impersonation.api_router)
# Phase 8 feature 2: admin account editor (identity + passwords).
app.include_router(identity_admin.api_router)
# Phase 8 feature 3/4: parked domains + whole-domain forwarding.
app.include_router(parked.api_router)
app.include_router(forwarding.api_router)
# Phase 8 feature 5/6: email delivery log + per-domain email routing.
app.include_router(email_extras.delivery_log_router)
app.include_router(email_extras.routing_router)
# Phase 8 feature 7: web terminal (WebSocket + a small session-count endpoint).
app.include_router(terminal.router)
app.include_router(terminal.http_router)
# Phase 8 features 8/9: WP-CLI + Composer.
app.include_router(devtools.wpcli_router)
app.include_router(devtools.composer_router)
# Phase 8 features 10/11/12: process manager, account notes, bulk actions.
app.include_router(processes.api_router)
app.include_router(notes.api_router)
app.include_router(bulkops.api_router)
# Cloudflare zone lifecycle (docs/PLAN-cloudflare.md Phase 1): zone-scoped
# enable/status/disable/purge under /api/v1/dns/zones/{domain}/cloudflare.
app.include_router(cloudflare.zone_api_router)
# File manager v2 (FileBrowser Quantum): the /launch entry point + the
# authenticated reverse proxy at /files. Registered explicitly (mixed prefixes:
# one under /api/v1, one at the /files root). The /files proxy is not shadowed
# by the /app SPA catch-all below, which is scoped to /app only.
app.include_router(filebrowser.api_router)
app.include_router(filebrowser.proxy_router)
# Run A feature 1: plan templates (CRUD + apply-to-account).
app.include_router(plans.api_router)
app.include_router(plans.apply_api_router)
# Run A feature 3: white-label branding (public GET, admin-only writes).
app.include_router(branding.api_router)
app.include_router(branding.admin_api_router)
# Run A feature 4: client onboarding wizard (once-only, account-scoped).
app.include_router(onboarding.api_router)
# Run A feature 5: service health monitoring (admin-only).
app.include_router(monitoring.api_router)
# Run A feature 7: admin view of the panel's own recent 5xx request errors.
app.include_router(adminlogs.api_router)
# Panel update system: authed version info + admin update check/apply/rollback.
app.include_router(update.api_router)
app.include_router(update.admin_api_router)
# Missing-features batch, goal feature 2: per-domain maintenance mode + admin
# overview of every domain currently in maintenance.
app.include_router(maintenance.api_router)
app.include_router(maintenance.admin_api_router)
# Missing-features batch, goal feature 3: wildcard domains.
app.include_router(wildcard.api_router)
# Missing-features batch, goal feature 4: custom error pages.
app.include_router(errorpages.api_router)
# Missing-features batch, goal feature 5: per-mailbox spam filters.
app.include_router(spamfilter.api_router)
# Missing-features batch, goal feature 1: IMAPSync migrations (account-scoped
# start/status/list + admin-wide view of every active job).
app.include_router(imapsync.api_router)
app.include_router(imapsync.admin_api_router)
# Missing-features batch, goal feature 6: per-domain site statistics + admin
# server-wide summary.
app.include_router(sitestats.api_router)
app.include_router(sitestats.admin_api_router)
# Missing-features batch, goal feature 7: live MariaDB monitor (admin-only).
app.include_router(dbmonitor.api_router)


@app.get("/")
def root():
    # The React SPA is the primary panel.
    return RedirectResponse("/app")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# --- Run A feature 8: admin-gated API documentation -----------------------
# Swagger UI at /api/docs, ReDoc at /api/redoc, both admin-only, both driven
# by the admin-only schema at /api/openapi.json. `get_identity`/`require_admin`
# raise 401/403 for anyone who isn't a signed-in admin -- the browser sends
# the same fh_session cookie when Swagger UI fetches the schema, so a
# logged-in admin sees the docs and no one else can even read the schema.
@app.get("/api/openapi.json", include_in_schema=False)
def openapi_schema(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return JSONResponse(app.openapi())


@app.get("/api/docs", include_in_schema=False)
def swagger_ui(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    # Assets are vendored under static/apidocs (see that dir's README) and
    # served same-origin, NOT from a CDN -- the panel must render offline on
    # the box it manages (README §7b), and the default jsdelivr URLs would be
    # blocked by this project's CSP anyway. The docs-page CSP is set in
    # _security_headers.
    return get_swagger_ui_html(
        openapi_url="/api/openapi.json",
        title="Forgehost API — Swagger UI",
        swagger_js_url="/static/apidocs/swagger-ui-bundle.js",
        swagger_css_url="/static/apidocs/swagger-ui.css",
        swagger_favicon_url="/static/apidocs/favicon-32x32.png",
    )


@app.get("/api/redoc", include_in_schema=False)
def redoc_ui(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return get_redoc_html(
        openapi_url="/api/openapi.json",
        title="Forgehost API — ReDoc",
        redoc_js_url="/static/apidocs/redoc.standalone.js",
        redoc_favicon_url="/static/apidocs/favicon-32x32.png",
        # Default True pulls Google Fonts from a CDN -- off for offline.
        with_google_fonts=False,
    )


# --- React SPA (Vite build in static/dist) --------------------------------
# The new control panel is a single-page app mounted under /app. Its assets are
# served by the /static mount above (they live in static/dist/assets/*); this
# catch-all returns the SPA's index.html for every /app/* path so client-side
# (React Router) deep links resolve. It is intentionally scoped to /app so it
# never shadows /api, /ui, /login, /logout, /static, or /healthz. No API
# behavior changes — this is pure static delivery of the built bundle.
SPA_INDEX = STATIC_DIR / "dist" / "index.html"


@app.get("/app")
@app.get("/app/{spa_path:path}")
def spa(spa_path: str = ""):
    if SPA_INDEX.is_file():
        # index.html must revalidate on every load: its asset URLs are content-
        # hashed, so a cached copy keeps serving an entire stale bundle after a
        # deploy. The hashed assets themselves stay long-cacheable.
        return FileResponse(str(SPA_INDEX), headers={"Cache-Control": "no-cache"})
    return PlainTextResponse("SPA build not found. Run `npm run build` in frontend/.", status_code=503)
