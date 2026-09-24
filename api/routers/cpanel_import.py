"""Phase 7b feature 1: cPanel/WHM full-backup-tarball import -- admin-only
(it always creates a brand-new Boron account, never touches an existing
customer's own account, so there is no customer-self-service angle at all).

An uploaded file is spooled to a plain temp file this process (boron-api,
unprivileged) can write -- borond (root) can read it regardless of which
uid wrote it, so no new shared-directory permission scheme is needed
(ARCHITECTURE.md SS2's privilege split is unaffected: boron-api still
never touches account-owned files or privileged state itself, it only hands
borond a path to a file it just wrote in the OS's own shared temp area).
"""
from __future__ import annotations

from pathlib import Path
import tempfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from starlette.requests import Request
from pydantic import BaseModel, Field

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin
from api.templates import templates
from shared.config import settings

api_router = APIRouter(prefix="/api/v1/admin/import/cpanel", tags=["cpanel-import"])
accounts_api_router = APIRouter(prefix="/api/v1/admin/import/accounts", tags=["account-imports"])
ui_router = APIRouter(prefix="/ui/admin/import/cpanel", tags=["ui:cpanel-import"])


def _spool_upload(file: UploadFile) -> str:
    """Streams the upload to disk in chunks (never reads the whole file into
    memory) and enforces the same size ceiling borond's own URL-download
    path uses, so an oversized upload is rejected here instead of only after
    boron-api has already buffered gigabytes of it."""
    max_bytes = settings.cpanel_import_max_upload_bytes
    fd, tmp_path = tempfile.mkstemp(suffix=".tar.gz", prefix="cpanel-import-")
    written = 0
    try:
        with open(fd, "wb") as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=413, detail=f"upload exceeds the {max_bytes} byte limit")
                out.write(chunk)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise
    return tmp_path


def _trigger(identity: Identity, username: str, url: str | None, file: UploadFile | None, panel: str = "cpanel") -> dict:
    if file is not None and file.filename:
        tmp_path = _spool_upload(file)
        try:
            return call_daemon("cpanel_import.trigger", identity, username=username, panel=panel, source="upload", source_ref=tmp_path)
        except Exception:
            # trigger_import validates before ever submitting the background
            # job -- a rejected request here (bad username, account already
            # exists, another import already running) means the daemon will
            # never read this file, so it must be cleaned up here instead of
            # leaking until borond's own post-extraction unlink (which
            # only runs for a job that actually started).
            Path(tmp_path).unlink(missing_ok=True)
            raise
    if url:
        return call_daemon("cpanel_import.trigger", identity, username=username, panel=panel, source="url", source_ref=url)
    raise HTTPException(status_code=400, detail="either a file upload or a url must be provided")


@api_router.post("")
def trigger_import(
    username: str = Form(...),
    url: str | None = Form(None),
    file: UploadFile | None = File(None),
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    return _trigger(identity, username, url, file)


@api_router.get("/{job_id}")
def get_import_job(job_id: int, username: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("cpanel_import.get", identity, job_id=job_id, username=username)


@api_router.get("")
def list_import_jobs(username: str | None = None, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("cpanel_import.list", identity, username=username)


@accounts_api_router.post("")
def trigger_account_import(
    username: str = Form(...),
    panel: str = Form(...),
    url: str | None = Form(None),
    file: UploadFile | None = File(None),
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    return _trigger(identity, username, url, file, panel=panel)


@accounts_api_router.get("")
def list_account_imports(username: str | None = None, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("cpanel_import.list", identity, username=username)


# Static paths precede the job route.


class DirectAdminConnection(BaseModel):
    mode: str = "admin"
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(default=2222, ge=1, le=65535)
    login: str = "admin"
    password: str = Field(min_length=1, max_length=4096)
    host_key: str = ""


class DirectAdminImport(BaseModel):
    remote: DirectAdminConnection
    remote_user: str
    username: str
    db_compatibility: str = "strict"


@accounts_api_router.post("/directadmin/inspect")
def inspect_directadmin(body: DirectAdminConnection, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("directadmin_remote.inspect", identity, **body.model_dump())


@accounts_api_router.post("/directadmin/migrate")
def migrate_directadmin(body: DirectAdminImport, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("cpanel_import.trigger", identity, panel="directadmin",
                       source="directadmin_remote", **body.model_dump())


@accounts_api_router.get("/{job_id}")
def get_account_import(job_id: int, username: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("cpanel_import.get", identity, job_id=job_id, username=username)


@ui_router.get("")
def ui_import_home(request: Request, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    jobs = call_daemon("cpanel_import.list", identity)["jobs"]
    return templates.TemplateResponse(request, "cpanel_import.html", {"identity": identity, "jobs": jobs})


@ui_router.post("")
def ui_trigger_import(
    username: str = Form(...),
    url: str = Form(""),
    file: UploadFile | None = File(None),
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    job = _trigger(identity, username, url or None, file)
    return RedirectResponse(f"/ui/admin/import/cpanel/{job['id']}?username={username}", status_code=303)


@ui_router.get("/{job_id}")
def ui_view_job(request: Request, job_id: int, username: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    job = call_daemon("cpanel_import.get", identity, job_id=job_id, username=username)
    return templates.TemplateResponse(request, "cpanel_import_job.html", {"identity": identity, "job": job})
