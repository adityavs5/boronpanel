"""Phase 5 feature 8: MySQL slow query viewer. Admin-only -- spans every
hosted database on the server, not a single account's resource.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/mysql/slow-queries", tags=["slow-queries"])
ui_router = APIRouter(prefix="/ui/slow-queries", tags=["ui:slow-queries"])


class BootstrapBody(BaseModel):
    confirm: bool = False


@api_router.get("/status")
def get_status(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("slowquery.status", identity)


@api_router.post("/bootstrap")
def bootstrap(body: BootstrapBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("slowquery.bootstrap", identity, confirm=body.confirm)


@api_router.get("")
def list_slow_queries(db: str = "", q: str = "", limit: int = 100, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("slowquery.list", identity, db=db, q=q, limit=limit)


# --- server-rendered UI ------------------------------------------------------


@ui_router.get("")
def ui_slow_queries(request: Request, db: str = "", q: str = "", identity: Identity = Depends(get_identity)):
    require_admin(identity)
    status = call_daemon("slowquery.status", identity)
    data = call_daemon("slowquery.list", identity, db=db, q=q, limit=100)
    return templates.TemplateResponse(
        request,
        "slow_queries.html",
        {"identity": identity, "status": status, "queries": data["queries"], "db": db, "q": q},
    )


@ui_router.post("/bootstrap")
def ui_bootstrap(confirm: str = Form(""), identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("slowquery.bootstrap", identity, confirm=confirm == "yes")
    return RedirectResponse("/ui/slow-queries", status_code=303)
