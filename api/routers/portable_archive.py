"""Admin export/import endpoints for portable Boron account archives."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from api.security import require_credential_origin
from starlette.requests import Request
from api.rpc import call_daemon
from api.upload_guard import BoundedUploadRoute
from api.security import Identity, get_identity, require_admin
from shared.config import settings

import_router = APIRouter(route_class=BoundedUploadRoute, prefix="/api/v1/admin/import/boron", tags=["account-archives"])
export_router = APIRouter(route_class=BoundedUploadRoute, prefix="/api/v1/admin/account-archives", tags=["account-archives"])
logger = logging.getLogger(__name__)


def _cleanup_export(identity: Identity, cleanup_id: str) -> None:
    try:
        call_daemon("portable.export.cleanup", identity, cleanup_id=cleanup_id)
    except Exception:
        logger.exception("Could not clean portable archive export %s", cleanup_id)


def _spool(file: UploadFile, identity: Identity) -> str:
    max_bytes = settings.cpanel_import_max_upload_bytes
    reserved = call_daemon("portable.import.upload.reserve", identity)
    name = reserved["path"]
    upload_id = reserved["upload_id"]
    written = 0
    try:
        fd = os.open(name, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
        with os.fdopen(fd, "wb") as output:
            while chunk := file.file.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=413, detail=f"upload exceeds the {max_bytes} byte limit")
                output.write(chunk)
    except Exception:
        try:
            call_daemon("portable.import.upload.cleanup", identity, upload_id=upload_id)
        except Exception:
            logger.exception("Could not clean failed portable archive upload %s", upload_id)
        raise
    return name


@import_router.post("")
def import_archive(username: str = Form(...), file: UploadFile = File(...), identity: Identity = Depends(get_identity)):
    require_admin(identity)
    path = _spool(file, identity)
    try:
        return call_daemon("portable.import.trigger", identity, username=username, source_ref=path)
    except Exception:
        try:
            call_daemon("portable.import.upload.cleanup", identity, upload_id=Path(path).name)
        except Exception:
            logger.exception("Could not clean rejected portable archive upload %s", Path(path).name)
        raise


@import_router.get("")
def list_imports(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("portable.import.list", identity)


@import_router.get("/{job_id}")
def get_import(request: Request, job_id: int, identity: Identity = Depends(get_identity)):
    require_credential_origin(request, identity)
    require_admin(identity)
    return call_daemon("portable.import.get", identity, job_id=job_id)


@export_router.get("/{job_id}/download")
def download_archive(job_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    prepared = call_daemon("portable.export.prepare", identity, job_id=job_id)
    return FileResponse(
        prepared["path"],
        filename=prepared["filename"],
        media_type="application/x-tar",
        background=BackgroundTask(_cleanup_export, identity, prepared["cleanup_id"]),
    )
