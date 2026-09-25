"""Administrator DNS operating-mode, staged migration, and diagnostics API."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

router = APIRouter(prefix="/api/v1/admin/dns-setup", tags=["dns"])


class ModeBody(BaseModel):
    mode: str
    local_nameservers: list[str] = Field(default_factory=list, min_length=0, max_length=8)


class MigrationBody(BaseModel):
    target: str
    confirm: bool = False


class DiagnosticsBody(BaseModel):
    zone: str | None = Field(default=None, max_length=253)


def _admin(identity: Identity) -> None:
    require_admin(identity)


@router.get("")
def settings(identity: Identity = Depends(get_identity)):
    _admin(identity)
    return call_daemon("dnssetup.get", identity)


@router.post("/mode/preview")
def preview_mode(body: ModeBody, identity: Identity = Depends(get_identity)):
    _admin(identity)
    return call_daemon("dnssetup.mode.preview", identity, **body.model_dump())


@router.put("/mode")
def save_mode(body: ModeBody, identity: Identity = Depends(get_identity)):
    _admin(identity)
    return call_daemon("dnssetup.mode.set", identity, **body.model_dump())


@router.post("/zones/{zone}/migration/preview")
def preview_zone(zone: str, body: MigrationBody, identity: Identity = Depends(get_identity)):
    _admin(identity)
    return call_daemon("dnssetup.zone.preview", identity, zone=zone, target=body.target)


@router.post("/zones/{zone}/migration")
def migrate_zone(zone: str, body: MigrationBody, identity: Identity = Depends(get_identity)):
    _admin(identity)
    return call_daemon(
        "dnssetup.zone.migrate", identity, zone=zone, target=body.target, confirm=body.confirm,
    )


@router.post("/zones/{zone}/migration/verify")
def verify_zone(zone: str, identity: Identity = Depends(get_identity)):
    _admin(identity)
    return call_daemon("dnssetup.zone.verify", identity, zone=zone)


@router.post("/diagnostics")
def diagnostics(body: DiagnosticsBody, identity: Identity = Depends(get_identity)):
    _admin(identity)
    return call_daemon("dnssetup.diagnostics", identity, **body.model_dump())
