from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin
from api.templates import templates

# Admin-level only: destinations/schedules are server-wide infra/policy,
# same trust level as suspend/terminate -- not a per-account self-service
# knob. Per-account trigger/browse/restore lives in account_backups.py
# (same split domains.py/dns.py already use for account-scoped vs.
# admin-scoped concerns under /accounts/{username}/...).
api_router = APIRouter(prefix="/api/v1/backups", tags=["backups"])
ui_router = APIRouter(prefix="/ui/backups", tags=["ui:backups"])


class CreateDestinationBody(BaseModel):
    name: str
    kind: str = "local"
    local_path: str | None = None
    rclone_remote_type: str | None = None
    rclone_config: dict = {}
    rclone_path_prefix: str = ""


@api_router.get("/destinations")
def list_destinations(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("backup.destination.list", identity)


@api_router.post("/destinations")
def create_destination(body: CreateDestinationBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("backup.destination.create", identity, **body.model_dump())


@api_router.delete("/destinations/{destination_id}")
def delete_destination(destination_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("backup.destination.delete", identity, id=destination_id)


class SetScheduleBody(BaseModel):
    username: str | None = None
    frequency: str = "daily"
    retention_count: int = 7
    destination_id: int
    enabled: bool = True


@api_router.get("/schedules")
def list_schedules(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("backup.schedule.list", identity)


@api_router.post("/schedules")
def set_schedule(body: SetScheduleBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("backup.schedule.set", identity, **body.model_dump())


@api_router.get("/jobs")
def list_all_jobs(username: str | None = None, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("backup.job.list", identity, username=username)


# --- server-rendered UI ------------------------------------------------------


@ui_router.get("")
def ui_admin_backups(request: Request, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    destinations = call_daemon("backup.destination.list", identity)["destinations"]
    schedules = call_daemon("backup.schedule.list", identity)["schedules"]
    jobs = call_daemon("backup.job.list", identity, username=None)["jobs"]
    return templates.TemplateResponse(
        request,
        "backups_admin.html",
        {"identity": identity, "destinations": destinations, "schedules": schedules, "jobs": jobs},
    )


@ui_router.post("/destinations")
def ui_create_destination(
    name: str = Form(...),
    kind: str = Form("local"),
    local_path: str = Form(""),
    rclone_remote_type: str = Form(""),
    rclone_path_prefix: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    call_daemon(
        "backup.destination.create",
        identity,
        name=name,
        kind=kind,
        local_path=local_path or None,
        rclone_remote_type=rclone_remote_type or None,
        rclone_path_prefix=rclone_path_prefix,
        rclone_config={},
    )
    return RedirectResponse("/ui/backups", status_code=303)


@ui_router.post("/destinations/{destination_id}/delete")
def ui_delete_destination(destination_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    call_daemon("backup.destination.delete", identity, id=destination_id)
    return RedirectResponse("/ui/backups", status_code=303)


@ui_router.post("/schedules")
def ui_set_schedule(
    username: str = Form(""),
    frequency: str = Form("daily"),
    retention_count: int = Form(7),
    destination_id: int = Form(...),
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    call_daemon(
        "backup.schedule.set",
        identity,
        username=username or None,
        frequency=frequency,
        retention_count=retention_count,
        destination_id=destination_id,
        enabled=True,
    )
    return RedirectResponse("/ui/backups", status_code=303)
