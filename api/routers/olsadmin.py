"""Administrator OpenLiteSpeed settings and WebAdmin credential controls."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

api_router = APIRouter(prefix="/api/v1/admin/openlitespeed", tags=["openlitespeed-admin"])


class SettingsBody(BaseModel):
    max_connections: int
    max_ssl_connections: int
    connection_timeout: int
    keep_alive_timeout: int
    max_keep_alive_requests: int
    gzip_level: int
    brotli_level: int
    gzip_enabled: bool
    brotli_enabled: bool
    quic_enabled: bool
    log_level: str
    log_keep_days: int


class ConfirmBody(BaseModel):
    confirm: bool = False


class ResetPasswordBody(BaseModel):
    username: str = "admin"


@api_router.get("")
def status(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("ols.admin.status", identity)


@api_router.put("/settings")
def update_settings(body: SettingsBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("ols.admin.settings.update", identity, **body.model_dump())


@api_router.post("/reload")
def reload_server(body: ConfirmBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("ols.admin.reload", identity, confirm=body.confirm)


@api_router.post("/credentials/reset")
def reset_password(body: ResetPasswordBody, response: Response, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    response.headers["Cache-Control"] = "no-store"
    return call_daemon("ols.admin.password.reset", identity, username=body.username)


@api_router.post("/credentials/reveal")
def reveal_password(body: ConfirmBody, response: Response, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    response.headers["Cache-Control"] = "no-store"
    return call_daemon("ols.admin.password.reveal", identity, confirm=body.confirm)
