"""Run A feature 5: service health monitoring. Admin-only -- host-wide
infrastructure, same posture as api/routers/health.py."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

api_router = APIRouter(prefix="/api/v1/admin/monitoring", tags=["monitoring"])


class MonitoringSettingsBody(BaseModel):
    enabled: bool | None = None
    admin_email: str | None = None
    cooldown_minutes: int | None = None


@api_router.get("/settings")
def get_settings(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("monitoring.settings.get", identity)


@api_router.patch("/settings")
def set_settings(body: MonitoringSettingsBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("monitoring.settings.set", identity, **body.model_dump(exclude_unset=True))


@api_router.get("/history")
def get_history(hours: int = 24, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("monitoring.history", identity, hours=hours)
