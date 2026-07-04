"""Phase 5 feature 9: panel-login IP whitelist. Admin-only. Enforcement
itself is a request-time middleware in `api/main.py`; this router is
just the CRUD surface for managing entries.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/security/ip-whitelist", tags=["ip-whitelist"])
ui_router = APIRouter(prefix="/ui/security/ip-whitelist", tags=["ui:ip-whitelist"])


class AddEntryBody(BaseModel):
    value: str
    note: str = ""


@api_router.get("")
def list_entries(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("ipwhitelist.list", identity)


@api_router.post("")
def add_entry(request: Request, body: AddEntryBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    requester_ip = request.client.host if request.client else None
    return call_daemon("ipwhitelist.add", identity, value=body.value, note=body.note, requester_ip=requester_ip)


@api_router.delete("/{entry_id}")
def delete_entry(entry_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("ipwhitelist.delete", identity, id=entry_id)


# --- server-rendered UI ------------------------------------------------------


@ui_router.get("")
def ui_list_entries(request: Request, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    data = call_daemon("ipwhitelist.list", identity)
    return templates.TemplateResponse(
        request,
        "ip_whitelist.html",
        {"identity": identity, "entries": data["entries"], "client_ip": request.client.host if request.client else ""},
    )


@ui_router.post("")
def ui_add_entry(request: Request, value: str = Form(...), note: str = Form(""), identity: Identity = Depends(get_identity)):
    require_admin(identity)
    requester_ip = request.client.host if request.client else None
    call_daemon("ipwhitelist.add", identity, value=value, note=note, requester_ip=requester_ip)
    return RedirectResponse("/ui/security/ip-whitelist", status_code=303)


@ui_router.post("/{entry_id}/delete")
def ui_delete_entry(entry_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("ipwhitelist.delete", identity, id=entry_id)
    return RedirectResponse("/ui/security/ip-whitelist", status_code=303)
