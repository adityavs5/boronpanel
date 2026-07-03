"""forgehost-api: the unprivileged REST API + admin UI process.

ARCHITECTURE.md SS2: runs as the `forgehost-api` system user, never root,
talks to forgehostd only over the Unix socket RPC (api/rpc.py). Every
state-changing endpoint is reached through the same get_identity/
require_*_access dependencies regardless of HTTP verb (SS9).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from api.routers import account_backups, accounts, apps, auth, backups, cron, databases, disktree, dns, domains, email, fileauth, files, ftp, git, hotlink, ipblock, logs_router, mail, nameservers, php_ini, pma, redirects, sshkeys, ssl_router, tokens, usage, wordpress

app = FastAPI(title="Forgehost", docs_url="/api/docs", redoc_url=None)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(auth.router)
for module in (accounts, domains, dns, databases, mail, ssl_router, files, cron, usage, backups, account_backups, tokens, wordpress, pma, email, ftp, php_ini, redirects, logs_router, hotlink, ipblock, fileauth, git, sshkeys, disktree, nameservers):
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
