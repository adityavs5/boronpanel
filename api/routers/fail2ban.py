"""Phase 5 feature 5: fail2ban. Admin-only, host-wide."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/fail2ban", tags=["fail2ban"])
ui_router = APIRouter(prefix="/ui/fail2ban", tags=["ui:fail2ban"])


class UnbanBody(BaseModel):
    ip: str


@api_router.get("")
def list_jails(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("fail2ban.list_jails", identity)


@api_router.get("/events")
def recent_events(jail: str | None = None, limit: int = 50, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("fail2ban.recent_events", identity, jail=jail, limit=limit)


@api_router.post("/bootstrap")
def bootstrap(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("fail2ban.bootstrap", identity)


@api_router.get("/{jail}")
def get_jail(jail: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("fail2ban.get_jail", identity, jail=jail)


@api_router.post("/{jail}/unban")
def unban_ip(jail: str, body: UnbanBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("fail2ban.unban_ip", identity, jail=jail, ip=body.ip)


@api_router.post("/{jail}/unban-all")
def unban_all(jail: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("fail2ban.unban_all", identity, jail=jail)


# --- server-rendered UI ------------------------------------------------------


@ui_router.get("")
def ui_fail2ban(request: Request, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    jails = call_daemon("fail2ban.list_jails", identity)["jails"]
    events = call_daemon("fail2ban.recent_events", identity, jail=None, limit=50)["events"]
    return templates.TemplateResponse(request, "fail2ban.html", {"identity": identity, "jails": jails, "events": events})


@ui_router.post("/bootstrap")
def ui_bootstrap(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("fail2ban.bootstrap", identity)
    return RedirectResponse("/ui/fail2ban", status_code=303)


@ui_router.post("/{jail}/unban")
def ui_unban_ip(jail: str, ip: str = Form(...), identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("fail2ban.unban_ip", identity, jail=jail, ip=ip)
    return RedirectResponse("/ui/fail2ban", status_code=303)


@ui_router.post("/{jail}/unban-all")
def ui_unban_all(jail: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("fail2ban.unban_all", identity, jail=jail)
    return RedirectResponse("/ui/fail2ban", status_code=303)
