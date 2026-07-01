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

from api.routers import accounts, auth, cron, databases, dns, domains, files, mail, ssl_router, tokens

app = FastAPI(title="Forgehost", docs_url="/api/docs", redoc_url=None)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(auth.router)
for module in (accounts, domains, dns, databases, mail, ssl_router, files, cron, tokens):
    app.include_router(module.api_router)
    app.include_router(module.ui_router)


@app.get("/")
def root():
    return RedirectResponse("/ui/accounts")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
