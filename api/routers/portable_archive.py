"""Admin export/import endpoints for portable Boron account archives."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin
from shared.config import settings

import_router = APIRouter(prefix="/api/v1/admin/import/boron", tags=["account-archives"])
export_router = APIRouter(prefix="/api/v1/admin/account-archives", tags=["account-archives"])


def _spool(file: UploadFile) -> str:
    max_bytes = settings.cpanel_import_max_upload_bytes
    fd, name = tempfile.mkstemp(prefix="boron-archive-import-", suffix=".boron.tar", dir="/tmp")
    written = 0
    try:
        with open(fd, "wb") as output:
            while chunk := file.file.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=413, detail=f"upload exceeds the {max_bytes} byte limit")
                output.write(chunk)
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise
    return name


@import_router.post("")
def import_archive(username: str = Form(...), file: UploadFile = File(...), identity: Identity = Depends(get_identity)):
    require_admin(identity)
    path = _spool(file)
    try:
        return call_daemon("portable.import.trigger", identity, username=username, source_ref=path)
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise


@import_router.get("")
def list_imports(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("portable.import.list", identity)


@import_router.get("/{job_id}")
def get_import(job_id: int, identity: Identity = Depends(get_identity)):
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
        background=BackgroundTask(shutil.rmtree, prepared["cleanup_dir"], ignore_errors=True),
    )
