"""Phase 7b feature 3: email notification settings (admin) + per-account
preferences (customer self-service + admin)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_admin
from api.templates import templates
from shared.models import NOTIFICATION_EVENT_TYPES

admin_api_router = APIRouter(prefix="/api/v1/admin/notifications", tags=["notifications"])
admin_ui_router = APIRouter(prefix="/ui/admin/notifications", tags=["ui:notifications"])
api_router = APIRouter(prefix="/api/v1/accounts/{username}/notification-prefs", tags=["notifications"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/notification-prefs", tags=["ui:notifications"])


class SettingsBody(BaseModel):
    sender_address: str = ""
    events: dict[str, bool] | None = None


class PrefsBody(BaseModel):
    customer_email: str | None = None
    events: dict[str, bool] | None = None


@admin_api_router.get("/settings")
def get_settings(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("notifications.settings.get", identity)


@admin_api_router.patch("/settings")
def set_settings(body: SettingsBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    # exclude_unset=True: daemon.notifications.set_settings distinguishes
    # "sender_address not provided, leave alone" from "provided as empty,
    # clear it" via `"sender_address" in params` -- a plain model_dump()
    # would always include the field (Pydantic fills in its "" default),
    # defeating that check for a genuinely partial PATCH (e.g. one that
    # only wants to flip an event's enabled state).
    return call_daemon("notifications.settings.set", identity, **body.model_dump(exclude_unset=True))


@admin_ui_router.get("/settings")
def ui_settings(request: Request, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    data = call_daemon("notifications.settings.get", identity)
    return templates.TemplateResponse(
        request, "notification_settings.html",
        {"identity": identity, "data": data, "event_types": NOTIFICATION_EVENT_TYPES},
    )


@admin_ui_router.post("/settings")
async def ui_set_settings(request: Request, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    # Event checkboxes share one field name ("events") with the event type
    # as the checkbox's own value, standard multi-checkbox HTML pattern --
    # read via the raw form (not typed Form() params) since event names
    # like "account.created" aren't valid Python parameter identifiers.
    form = await request.form()
    checked = set(form.getlist("events"))
    events = {event: (event in checked) for event in NOTIFICATION_EVENT_TYPES}
    call_daemon(
        "notifications.settings.set", identity,
        sender_address=form.get("sender_address", ""), events=events,
    )
    return RedirectResponse("/ui/admin/notifications/settings", status_code=303)


@api_router.get("")
def get_prefs(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("notifications.prefs.get", identity, username=username)


@api_router.patch("")
def set_prefs(username: str, body: PrefsBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    # exclude_unset=True: same partial-update reasoning as set_settings above.
    return call_daemon("notifications.prefs.set", identity, username=username, **body.model_dump(exclude_unset=True))


@ui_router.get("")
def ui_prefs(request: Request, username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    data = call_daemon("notifications.prefs.get", identity, username=username)
    return templates.TemplateResponse(
        request, "notification_prefs.html",
        {"identity": identity, "username": username, "data": data, "event_types": NOTIFICATION_EVENT_TYPES},
    )


@ui_router.post("")
async def ui_set_prefs(request: Request, username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    form = await request.form()
    checked = set(form.getlist("events"))
    events = {event: (event in checked) for event in NOTIFICATION_EVENT_TYPES}
    call_daemon(
        "notifications.prefs.set", identity, username=username,
        customer_email=form.get("customer_email", "") or None, events=events,
    )
    return RedirectResponse(f"/ui/accounts/{username}/notification-prefs", status_code=303)
