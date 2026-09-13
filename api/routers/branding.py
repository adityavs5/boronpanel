"""Run A feature 3: white-label branding.

The settings + asset endpoints are deliberately split into two trust
levels: the GET endpoints are PUBLIC (no `get_identity` dependency at
all) -- the login page, browser tab favicon, and sidebar all need to show
the branding before any session exists. Every write (settings PATCH,
logo/favicon upload/remove) is `require_admin`. Asset bytes are streamed
by the API process reading the file directly (same "unprivileged API
reads the DB/its own group-readable files directly, only writes go
through the daemon RPC" convention `api/routers/accounts.py`'s
`list_accounts` already uses for a direct `read_session` query) --
`borond` never streams file bytes over the RPC channel."""
from __future__ import annotations

import base64
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from shared.config import settings
from shared.db import read_session
from shared.models import BrandingSettings

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

api_router = APIRouter(prefix="/api/v1/branding", tags=["branding"])
admin_api_router = APIRouter(prefix="/api/v1/admin/branding", tags=["branding"])

_MEDIA_TYPES = {"png": "image/png", "svg": "image/svg+xml", "ico": "image/x-icon"}


def _get_row(db) -> BrandingSettings | None:
    return db.get(BrandingSettings, 1)


@api_router.get("")
def get_branding():
    with read_session() as db:
        row = _get_row(db)
        if row is None:
            return {
                "panel_name": "Boron", "support_email": None, "support_url": None,
                "logo_url": None, "favicon_url": None,
            }
        return {
            "panel_name": row.panel_name,
            "support_email": row.support_email,
            "support_url": row.support_url,
            "logo_url": "/api/v1/branding/logo" if row.logo_filename else None,
            "favicon_url": "/api/v1/branding/favicon" if row.favicon_filename else None,
        }


def _serve_asset(field: str):
    """SVG can embed <script>/event-handler XSS -- three independent layers
    guard against it, not just daemon.branding's upload-time reject filter
    (which only blocks the obvious cases, not a full sanitizer): (1) this
    path isn't `/app` or the filebrowser proxy, so `api/main.py`'s
    `_security_headers` middleware's default branch unconditionally sets
    `Content-Security-Policy: script-src 'none'` on this response --
    verified by `tests/test_branding.py::test_logo_response_has_restrictive_csp`,
    not assumed; (2) every caller renders this URL via an `<img>` tag
    (Sidebar.jsx/Login.jsx), and browsers never execute script inside an
    SVG loaded as an `<img>` (only `<object>`/`<iframe>`/direct navigation
    do) -- so even a malicious SVG that slipped past the upload-time filter
    cannot execute in the panel's own document that way; (3) the upload
    reject filter itself, as a first line of defense against storing an
    obviously malicious file at all."""
    with read_session() as db:
        row = _get_row(db)
        filename = getattr(row, f"{field}_filename", None) if row else None
    if filename is None:
        raise HTTPException(status_code=404, detail=f"no {field} configured")
    path = Path(settings.branding_dir) / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no {field} configured")
    ext = filename.rsplit(".", 1)[-1]
    return FileResponse(path, media_type=_MEDIA_TYPES.get(ext, "application/octet-stream"))


@api_router.get("/logo")
def get_logo():
    return _serve_asset("logo")


@api_router.get("/favicon")
def get_favicon():
    return _serve_asset("favicon")


@admin_api_router.get("")
def get_admin_branding(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("branding.get", identity)


class BrandingBody(BaseModel):
    terminal_banner: str | None = None
    panel_name: str | None = None
    support_email: str | None = None
    support_url: str | None = None


@admin_api_router.patch("")
def set_branding(body: BrandingBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("branding.set", identity, **body.model_dump(exclude_unset=True))


async def _upload(op: str, file: UploadFile, identity: Identity):
    require_admin(identity)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="uploaded file is empty")
    image_base64 = base64.b64encode(data).decode("ascii")
    return call_daemon(op, identity, image_base64=image_base64)


@admin_api_router.post("/logo")
async def upload_logo(file: UploadFile = File(...), identity: Identity = Depends(get_identity)):
    return await _upload("branding.logo.upload", file, identity)


@admin_api_router.post("/favicon")
async def upload_favicon(file: UploadFile = File(...), identity: Identity = Depends(get_identity)):
    return await _upload("branding.favicon.upload", file, identity)


@admin_api_router.delete("/logo")
def remove_logo(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("branding.logo.remove", identity)


@admin_api_router.delete("/favicon")
def remove_favicon(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("branding.favicon.remove", identity)
