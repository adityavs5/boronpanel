"""Resumable administrator server-setup wizard API."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

router = APIRouter(prefix="/api/v1/admin/server-setup", tags=["panel configuration"])


class StepBody(BaseModel):
    model_config = ConfigDict(extra="allow")
    step: int = Field(ge=1, le=8)


class PreviewRecordsBody(BaseModel):
    panel_hostname: str | None = None
    webmail_hostname: str | None = None
    pma_hostname: str | None = None
    server_public_ip: str | None = None


def _admin(identity: Identity) -> None:
    require_admin(identity)


@router.get("")
def status(identity: Identity = Depends(get_identity)):
    _admin(identity)
    return call_daemon("server_setup.status", identity)


@router.post("/steps")
def run_step(body: StepBody, identity: Identity = Depends(get_identity)):
    _admin(identity)
    return call_daemon("server_setup.step", identity, **body.model_dump())


@router.post("/service-records/preview")
def preview_records(body: PreviewRecordsBody, identity: Identity = Depends(get_identity)):
    _admin(identity)
    return call_daemon("server_setup.records.preview", identity, **body.model_dump())


@router.delete("/steps/{step}")
def reset_step(step: int, identity: Identity = Depends(get_identity)):
    _admin(identity)
    return call_daemon("server_setup.step.reset", identity, step=step)
