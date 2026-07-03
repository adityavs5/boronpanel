"""Phase 5 feature 4: firewall UI (UFW). Admin-only, host-wide."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/firewall", tags=["firewall"])
ui_router = APIRouter(prefix="/ui/firewall", tags=["ui:firewall"])


class AddRuleBody(BaseModel):
    action: str
    port: int
    protocol: str = "any"
    from_addr: str = "any"
    comment: str = ""


class ConfirmBody(BaseModel):
    confirm: bool = False


@api_router.get("/rules")
def list_rules(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("firewall.list", identity)


@api_router.post("/rules")
def add_rule(body: AddRuleBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("firewall.add", identity, **body.model_dump())


@api_router.delete("/rules/{rule_id}")
def delete_rule(rule_id: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("firewall.delete", identity, rule_id=rule_id)


@api_router.get("/status")
def get_status(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("firewall.status", identity)


@api_router.post("/enable")
def enable(body: ConfirmBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("firewall.enable", identity, confirm=body.confirm)


@api_router.post("/disable")
def disable(body: ConfirmBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("firewall.disable", identity, confirm=body.confirm)


# --- server-rendered UI ------------------------------------------------------


@ui_router.get("")
def ui_firewall(request: Request, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    data = call_daemon("firewall.list", identity)
    return templates.TemplateResponse(
        request, "firewall.html", {"identity": identity, "rules": data["rules"], "active": data["active"]}
    )


@ui_router.post("/rules")
def ui_add_rule(
    action: str = Form(...),
    port: int = Form(...),
    protocol: str = Form("any"),
    from_addr: str = Form("any"),
    comment: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    call_daemon("firewall.add", identity, action=action, port=port, protocol=protocol, from_addr=from_addr, comment=comment)
    return RedirectResponse("/ui/firewall", status_code=303)


@ui_router.post("/rules/{rule_id}/delete")
def ui_delete_rule(rule_id: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("firewall.delete", identity, rule_id=rule_id)
    return RedirectResponse("/ui/firewall", status_code=303)


@ui_router.post("/enable")
def ui_enable(confirm: str = Form(""), identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("firewall.enable", identity, confirm=confirm == "yes")
    return RedirectResponse("/ui/firewall", status_code=303)


@ui_router.post("/disable")
def ui_disable(confirm: str = Form(""), identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("firewall.disable", identity, confirm=confirm == "yes")
    return RedirectResponse("/ui/firewall", status_code=303)
