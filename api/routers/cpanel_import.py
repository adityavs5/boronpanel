"""Phase 7b feature 1: cPanel/WHM full-backup-tarball import -- admin-only
(it always creates a brand-new Boron account, never touches an existing
customer's own account, so there is no customer-self-service angle at all).

An uploaded file is streamed into a spool file borond reserves for this
process (``cpanel_import.upload.reserve``: root-prepared upload directory,
boron-api-owned 0600 file). borond only ever reads -- and later deletes --
a path that passes that spool validation, so this unprivileged process
cannot point root at an arbitrary file (and boron-api's PrivateTmp /tmp
is not visible to borond anyway).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from starlette.requests import Request
from pydantic import BaseModel, Field

from api.security import require_credential_origin
from api.rpc import call_daemon
from api.upload_guard import BoundedUploadRoute
from api.security import Identity, get_identity, require_admin
from api.templates import templates
from shared.config import settings

api_router = APIRouter(route_class=BoundedUploadRoute, prefix="/api/v1/admin/import/cpanel", tags=["cpanel-import"])
accounts_api_router = APIRouter(route_class=BoundedUploadRoute, prefix="/api/v1/admin/import/accounts", tags=["account-imports"])
ui_router = APIRouter(route_class=BoundedUploadRoute, prefix="/ui/admin/import/cpanel", tags=["ui:cpanel-import"])
logger = logging.getLogger(__name__)


def _cleanup_upload(identity: Identity, upload_id: str) -> None:
    try:
        call_daemon("cpanel_import.upload.cleanup", identity, upload_id=upload_id)
    except Exception:
        logger.exception("Could not clean backup upload %s", upload_id)


def _spool_upload(file: UploadFile, identity: Identity) -> str:
    """Streams the upload to disk in chunks (never reads the whole file into
    memory) and enforces the same size ceiling borond's own URL-download
    path uses, so an oversized upload is rejected here instead of only after
    boron-api has already buffered gigabytes of it."""
    max_bytes = settings.cpanel_import_max_upload_bytes
    reserved = call_daemon("cpanel_import.upload.reserve", identity)
    tmp_path = reserved["path"]
    written = 0
    try:
        fd = os.open(tmp_path, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = file.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=413, detail=f"upload exceeds the {max_bytes} byte limit")
                out.write(chunk)
    except Exception:
        _cleanup_upload(identity, reserved["upload_id"])
        raise
    return tmp_path


def _trigger(identity: Identity, username: str, url: str | None, file: UploadFile | None, panel: str = "cpanel") -> dict:
    if file is not None and file.filename:
        tmp_path = _spool_upload(file, identity)
        try:
            return call_daemon("cpanel_import.trigger", identity, username=username, panel=panel, source="upload", source_ref=tmp_path)
        except Exception:
            # trigger_import validates before ever submitting the background
            # job -- a rejected request here (bad username, account already
            # exists, another import already running) means the daemon will
            # never read this file, so it must be cleaned up here instead of
            # leaking until borond's own post-extraction cleanup (which
            # only runs for a job that actually started).
            _cleanup_upload(identity, Path(tmp_path).name)
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
def get_import_job(request: Request, job_id: int, username: str, identity: Identity = Depends(get_identity)):
    require_credential_origin(request, identity)
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
    preflight_only: bool = False


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
def get_account_import(request: Request, job_id: int, username: str, identity: Identity = Depends(get_identity)):
    require_credential_origin(request, identity)
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
    require_credential_origin(request, identity)
    require_admin(identity)
    job = call_daemon("cpanel_import.get", identity, job_id=job_id, username=username)
    return templates.TemplateResponse(request, "cpanel_import_job.html", {"identity": identity, "job": job})
