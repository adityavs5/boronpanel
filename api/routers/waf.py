"""Phase 5 feature 7: ModSecurity/WAF. Admin-only."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/waf", tags=["waf"])
ui_router = APIRouter(prefix="/ui/waf", tags=["ui:waf"])


class SetEnabledBody(BaseModel):
    enabled: bool


class DomainOverrideBody(BaseModel):
    domain: str
    disabled: bool = True


class CustomRuleBody(BaseModel):
    domain: str
    target: str
    pattern: str


class WafSettingsBody(BaseModel):
    mode: str
    paranoia_level: int = 1
    anomaly_threshold: int = 5
    wp_login_limit: int = 10
    wp_xmlrpc_limit: int = 5
    wp_rate_window_seconds: int = 60


class DomainPolicyBody(BaseModel):
    domain: str
    mode: str = "inherit"


class WafExceptionBody(BaseModel):
    domain: str
    rule_id: int | None = None
    category: str | None = None
    uri_prefix: str | None = None
    parameter: str | None = None
    duration_hours: int = 24
    reason: str = ""


class UnblockBody(BaseModel):
    ip: str


@api_router.get("")
def get_status(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("waf.status", identity)


@api_router.post("/enable")
def set_enabled(body: SetEnabledBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("waf.set_enabled", identity, enabled=body.enabled)


@api_router.put("/settings")
def update_settings(body: WafSettingsBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("waf.settings.update", identity, **body.model_dump())


@api_router.put("/domain-policy")
def set_domain_policy(body: DomainPolicyBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("waf.domain_policy.set", identity, **body.model_dump())


@api_router.post("/domain-override")
def set_domain_override(body: DomainOverrideBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("waf.set_domain_override", identity, domain=body.domain, disabled=body.disabled)


@api_router.post("/custom-rules")
def add_custom_rule(body: CustomRuleBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("waf.add_custom_rule", identity, domain=body.domain, target=body.target, pattern=body.pattern)


@api_router.delete("/custom-rules/{rule_id}")
def delete_custom_rule(rule_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("waf.delete_custom_rule", identity, rule_id=rule_id)


@api_router.post("/exceptions")
def add_exception(body: WafExceptionBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("waf.exception.add", identity, **body.model_dump())


@api_router.delete("/exceptions/{exception_id}")
def delete_exception(exception_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("waf.exception.delete", identity, exception_id=exception_id)


@api_router.post("/incidents/unblock")
def unblock_incident_ip(body: UnblockBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("waf.incident.unblock", identity, ip=body.ip)


@api_router.get("/blocked-requests")
def blocked_requests(domain: str | None = None, limit: int = 50, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("waf.blocked_requests", identity, domain=domain, limit=limit)


# --- server-rendered UI ------------------------------------------------------


@ui_router.get("")
def ui_waf(request: Request, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    status = call_daemon("waf.status", identity)
    events = call_daemon("waf.blocked_requests", identity, domain=None, limit=50)["events"]
    return templates.TemplateResponse(request, "waf.html", {"identity": identity, "status": status, "events": events})


@ui_router.post("/enable")
def ui_set_enabled(enabled: str = Form(""), identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("waf.set_enabled", identity, enabled=enabled == "yes")
    return RedirectResponse("/ui/waf", status_code=303)


@ui_router.post("/domain-override")
def ui_set_domain_override(domain: str = Form(...), disabled: str = Form("yes"), identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("waf.set_domain_override", identity, domain=domain, disabled=disabled == "yes")
    return RedirectResponse("/ui/waf", status_code=303)


@ui_router.post("/custom-rules")
def ui_add_custom_rule(domain: str = Form(...), target: str = Form(...), pattern: str = Form(...), identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("waf.add_custom_rule", identity, domain=domain, target=target, pattern=pattern)
    return RedirectResponse("/ui/waf", status_code=303)


@ui_router.post("/custom-rules/{rule_id}/delete")
def ui_delete_custom_rule(rule_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("waf.delete_custom_rule", identity, rule_id=rule_id)
    return RedirectResponse("/ui/waf", status_code=303)
