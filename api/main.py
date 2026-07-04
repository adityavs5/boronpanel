"""forgehost-api: the unprivileged REST API + admin UI process.

ARCHITECTURE.md SS2: runs as the `forgehost-api` system user, never root,
talks to forgehostd only over the Unix socket RPC (api/rpc.py). Every
state-changing endpoint is reached through the same get_identity/
require_*_access dependencies regardless of HTTP verb (SS9).
"""
from __future__ import annotations

from pathlib import Path

import ipaddress

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from shared.db import read_session
from shared.models import IpWhitelistEntry

from api.routers import account_backups, accounts, apps, auditlog, auth, backups, cron, databases, disktree, dns, domains, email, fail2ban, fileauth, files, firewall, ftp, git, health, hotlink, ipblock, ipwhitelist, logs_router, mail, mailqueue, nameservers, php_ini, pma, redirects, services, slowquery, sshkeys, ssl_router, tokens, twofactor, usage, waf, wordpress

app = FastAPI(title="Forgehost", docs_url="/api/docs", redoc_url=None)

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
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'none'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    return response

app.include_router(auth.router)
for module in (accounts, domains, dns, databases, mail, ssl_router, files, cron, usage, backups, account_backups, tokens, wordpress, pma, email, ftp, php_ini, redirects, logs_router, hotlink, ipblock, fileauth, git, sshkeys, disktree, nameservers, health, services, mailqueue, firewall, fail2ban, auditlog, waf, slowquery, ipwhitelist, twofactor):
    app.include_router(module.api_router)
    app.include_router(module.ui_router)
# Phase 4 feature 8: apps.py has three router objects (account-scoped
# "list installed", domain-scoped "install"/"jobs", and its UI) --
# doesn't fit the uniform api_router/ui_router pair the loop above
# assumes, same reason ssl_router/mail get an explicit extra
# app.include_router call each.
app.include_router(apps.api_router)
app.include_router(apps.domain_api_router)
app.include_router(apps.ui_router)
# Phase 3 feature 8: ssl_router's account-scoped API surface
# (/accounts/{u}/ssl, /accounts/{u}/domains/{d}/ssl/issue) is a separate
# router object, not the module's main api_router/ui_router pair.
app.include_router(ssl_router.account_api_router)
# Phase 3 feature 10: same pattern for mail's account-scoped password
# manager endpoint (/accounts/{u}/email/{m}/password).
app.include_router(mail.account_api_router)
# Phase 4 feature 1: SpamAssassin's server-wide admin default threshold.
app.include_router(email.admin_router)


@app.get("/")
def root():
    return RedirectResponse("/ui/accounts")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
