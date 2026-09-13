from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access
from api.templates import templates

# Self-service, not admin-only: triggering/browsing/restoring one's own
# backups matches JetBackup's own customer-facing restore feature, same
# reasoning as Phase 2 feature 1's php-version endpoint. Destination/
# schedule configuration (server-wide infra/policy) stays admin-only in
# backups.py.
api_router = APIRouter(prefix="/api/v1/accounts/{username}/backups", tags=["backups"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/backups", tags=["ui:backups"])


class TriggerBackupBody(BaseModel):
    kind: str = "full"
    item_ref: str | None = None
    destination_id: int | None = None


@api_router.post("")
def trigger_backup(username: str, body: TriggerBackupBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("backup.job.trigger", identity, username=username, **body.model_dump())


@api_router.get("")
def list_account_jobs(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("backup.job.list", identity, username=username)


@api_router.get('/snapshots/runs')
def list_snapshot_runs(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon('snapshot.run.list', identity, username=username)


@api_router.get('/snapshots/runs/{run_id}/browse')
def browse_snapshot(username: str, run_id: int, directory: str = '/', identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon('snapshot.run.browse', identity, username=username, run_id=run_id, directory=directory)


@api_router.get('/snapshots/runs/{run_id}/databases')
def snapshot_databases(username: str, run_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon('snapshot.restore.databases', identity, username=username, run_id=run_id)


@api_router.get('/snapshots/runs/{run_id}/mailboxes')
def snapshot_mailboxes(username: str, run_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon('snapshot.restore.mailboxes', identity, username=username, run_id=run_id)


class SnapshotRestoreBody(BaseModel):
    confirmation: str
    kind: str = 'files'
    paths: list[str] = []
    databases: list[str] = []


@api_router.post('/snapshots/runs/{run_id}/restore')
def restore_snapshot(username: str, run_id: int, body: SnapshotRestoreBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon('snapshot.restore.trigger', identity, username=username, run_id=run_id, **body.model_dump())


@api_router.get('/snapshots/restores')
def snapshot_restore_history(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon('snapshot.restore.list', identity, username=username)


@api_router.post('/snapshots/restores/{restore_id}/undo')
def undo_snapshot_restore(username: str, restore_id: int, body: SnapshotRestoreBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon('snapshot.restore.undo', identity, username=username, restore_id=restore_id, confirmation=body.confirmation)


@api_router.get("/{job_id}")
def get_job(username: str, job_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("backup.job.get", identity, username=username, job_id=job_id)


@api_router.get("/{job_id}/browse")
def browse_backup(username: str, job_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("backup.job.browse", identity, username=username, job_id=job_id)


class TriggerRestoreBody(BaseModel):
    kind: str | None = None
    item_ref: str | None = None


@api_router.post("/{job_id}/restore")
def trigger_restore(username: str, job_id: int, body: TriggerRestoreBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("backup.restore.trigger", identity, username=username, backup_job_id=job_id, **body.model_dump())


@api_router.get("/restores/list")
def list_account_restores(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("backup.restore.list", identity, username=username)


# --- server-rendered UI ------------------------------------------------------


@ui_router.get("")
def ui_account_backups(request: Request, username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    jobs = call_daemon("backup.job.list", identity, username=username)["jobs"]
    destinations = call_daemon("backup.destination.list", identity)["destinations"]
    return templates.TemplateResponse(
        request,
        "account_backups.html",
        {"identity": identity, "username": username, "jobs": jobs, "destinations": destinations},
    )


@ui_router.post("")
def ui_trigger_backup(
    username: str,
    kind: str = Form("full"),
    item_ref: str = Form(""),
    destination_id: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    call_daemon(
        "backup.job.trigger",
        identity,
        username=username,
        kind=kind,
        item_ref=item_ref or None,
        destination_id=int(destination_id) if destination_id else None,
    )
    return RedirectResponse(f"/ui/accounts/{username}/backups", status_code=303)


@ui_router.get("/{job_id}")
def ui_browse_backup(request: Request, username: str, job_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    job = call_daemon("backup.job.get", identity, username=username, job_id=job_id)
    browse = call_daemon("backup.job.browse", identity, username=username, job_id=job_id) if job["status"] == "completed" else None
    return templates.TemplateResponse(
        request,
        "backup_browse.html",
        {"identity": identity, "username": username, "job": job, "browse": browse},
    )


@ui_router.post("/{job_id}/restore")
def ui_trigger_restore(
    username: str,
    job_id: int,
    kind: str = Form(""),
    item_ref: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    call_daemon(
        "backup.restore.trigger",
        identity,
        username=username,
        backup_job_id=job_id,
        kind=kind or None,
        item_ref=item_ref or None,
    )
    return RedirectResponse(f"/ui/accounts/{username}/backups", status_code=303)
