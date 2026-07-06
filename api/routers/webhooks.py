"""Phase 7b feature 4: admin-only outbound webhook CRUD + delivery log +
test button."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin
from api.templates import templates
from shared.models import WEBHOOK_EVENT_TYPES

api_router = APIRouter(prefix="/api/v1/admin/webhooks", tags=["webhooks"])
ui_router = APIRouter(prefix="/ui/admin/webhooks", tags=["ui:webhooks"])


class CreateWebhookBody(BaseModel):
    url: str
    events: list[str]
    secret: str | None = None
    enabled: bool = True


class UpdateWebhookBody(BaseModel):
    url: str | None = None
    events: list[str] | None = None
    enabled: bool | None = None


@api_router.post("")
def create_webhook(body: CreateWebhookBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("webhooks.create", identity, **body.model_dump())


@api_router.get("")
def list_webhooks(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("webhooks.list", identity)


@api_router.get("/{webhook_id}")
def get_webhook(webhook_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("webhooks.get", identity, webhook_id=webhook_id)


@api_router.patch("/{webhook_id}")
def update_webhook(webhook_id: int, body: UpdateWebhookBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("webhooks.update", identity, webhook_id=webhook_id, **body.model_dump(exclude_unset=True))


@api_router.delete("/{webhook_id}")
def delete_webhook(webhook_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("webhooks.delete", identity, webhook_id=webhook_id)


@api_router.get("/{webhook_id}/deliveries")
def list_deliveries(webhook_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("webhooks.deliveries.list", identity, webhook_id=webhook_id)


@api_router.post("/{webhook_id}/test")
def test_webhook(webhook_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("webhooks.test", identity, webhook_id=webhook_id)


@ui_router.get("")
def ui_webhooks_home(request: Request, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    webhooks = call_daemon("webhooks.list", identity)["webhooks"]
    return templates.TemplateResponse(
        request, "webhooks.html", {"identity": identity, "webhooks": webhooks, "event_types": WEBHOOK_EVENT_TYPES}
    )


@ui_router.post("")
async def ui_create_webhook(request: Request, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    form = await request.form()
    events = form.getlist("events")
    call_daemon(
        "webhooks.create", identity,
        url=form.get("url", ""), events=events, secret=form.get("secret") or None,
    )
    return RedirectResponse("/ui/admin/webhooks", status_code=303)


@ui_router.post("/{webhook_id}/delete")
def ui_delete_webhook(webhook_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("webhooks.delete", identity, webhook_id=webhook_id)
    return RedirectResponse("/ui/admin/webhooks", status_code=303)


@ui_router.post("/{webhook_id}/toggle")
def ui_toggle_webhook(webhook_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    current = call_daemon("webhooks.get", identity, webhook_id=webhook_id)
    call_daemon("webhooks.update", identity, webhook_id=webhook_id, enabled=not current["enabled"])
    return RedirectResponse("/ui/admin/webhooks", status_code=303)


@ui_router.post("/{webhook_id}/test")
def ui_test_webhook(webhook_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("webhooks.test", identity, webhook_id=webhook_id)
    return RedirectResponse(f"/ui/admin/webhooks/{webhook_id}/deliveries", status_code=303)


@ui_router.get("/{webhook_id}/deliveries")
def ui_deliveries(request: Request, webhook_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    webhook = call_daemon("webhooks.get", identity, webhook_id=webhook_id)
    deliveries = call_daemon("webhooks.deliveries.list", identity, webhook_id=webhook_id)["deliveries"]
    return templates.TemplateResponse(
        request, "webhook_deliveries.html", {"identity": identity, "webhook": webhook, "deliveries": deliveries}
    )
