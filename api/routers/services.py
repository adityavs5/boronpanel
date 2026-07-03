"""Phase 5 feature 2: service manager. Admin-only, same posture as
health.py -- host-wide infrastructure control, not a per-account resource.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/services", tags=["services"])
ui_router = APIRouter(prefix="/ui/services", tags=["ui:services"])


class ControlBody(BaseModel):
    confirm: bool = False


@api_router.get("")
def list_services(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("services.list", identity)


@api_router.get("/{service}")
def get_service(service: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("services.status", identity, service=service)


@api_router.post("/{service}/{action}")
def control_service(service: str, action: str, body: ControlBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("services.control", identity, service=service, action=action, confirm=body.confirm)


# --- server-rendered UI ------------------------------------------------------


@ui_router.get("")
def ui_services(request: Request, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    services = call_daemon("services.list", identity)["services"]
    return templates.TemplateResponse(request, "services.html", {"identity": identity, "services": services})


@ui_router.get("/{service}")
def ui_service_detail(request: Request, service: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    detail = call_daemon("services.status", identity, service=service)
    return templates.TemplateResponse(request, "service_detail.html", {"identity": identity, "detail": detail})


@ui_router.post("/{service}/{action}")
def ui_control_service(service: str, action: str, confirm: str = Form(""), identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("services.control", identity, service=service, action=action, confirm=confirm == "yes")
    return RedirectResponse(f"/ui/services/{service}", status_code=303)
