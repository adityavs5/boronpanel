"""Missing-features batch, goal feature 6: per-domain site statistics.
GET /accounts/{u}/domains/{d}/stats?period=daily|weekly|monthly (the goal's
own literal shape), plus an admin-only server-wide summary and MaxMind
license-key configuration endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_admin, require_domain_access

api_router = APIRouter(prefix="/api/v1/accounts/{username}/domains/{domain}/stats", tags=["sitestats"])
admin_api_router = APIRouter(prefix="/api/v1/admin/sitestats", tags=["sitestats"])


class ConfigureGeoipBody(BaseModel):
    license_key: str


@api_router.get("")
def get_stats(username: str, domain: str, period: str = "daily", identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("sitestats.get", identity, domain=domain, period=period)


@admin_api_router.get("/summary")
def get_admin_summary(period: str = "daily", identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("sitestats.admin_summary", identity, period=period)


@admin_api_router.post("/geoip")
def configure_geoip(body: ConfigureGeoipBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("sitestats.configure_geoip", identity, license_key=body.license_key)
