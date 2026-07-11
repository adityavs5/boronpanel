"""QA round 2, item 10: admin-editable suspension page + welcome email
template. Both are server-wide, admin-only -- distinct from the per-domain
custom error pages (api/routers/errorpages.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

api_router = APIRouter(prefix="/api/v1/admin/templates", tags=["site-templates"])


class SuspendedPageBody(BaseModel):
    content: str


class WelcomeEmailBody(BaseModel):
    subject: str | None = None
    body: str | None = None


@api_router.get("/suspended-page")
def get_suspended_page(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("templates.suspended_page.get", identity)


@api_router.put("/suspended-page")
def set_suspended_page(body: SuspendedPageBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("templates.suspended_page.set", identity, content=body.content)


@api_router.get("/welcome-email")
def get_welcome_email(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("templates.welcome_email.get", identity)


@api_router.put("/welcome-email")
def set_welcome_email(body: WelcomeEmailBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("templates.welcome_email.set", identity, subject=body.subject, body=body.body)


@api_router.delete("/welcome-email")
def reset_welcome_email(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("templates.welcome_email.reset", identity)
