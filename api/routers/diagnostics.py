"""Administrator diagnostics, readiness, and one-time support bundles."""
from __future__ import annotations

import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin
from shared.config import settings

api_router = APIRouter(prefix="/api/v1/admin", tags=["diagnostics"])


class SupportBundleBody(BaseModel):
    include_names: bool = False


def _require_admin_session(identity: Identity) -> None:
    require_admin(identity)
    if identity.auth_method != "session" or identity.panel_user_id <= 0 or identity.is_impersonating:
        raise HTTPException(status_code=403, detail="A current administrator session is required")


@api_router.get("/readiness")
def readiness(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("diagnostics.run", identity)


@api_router.post("/diagnostics/run")
def run_diagnostics(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("diagnostics.run", identity)


@api_router.post("/support-bundles")
def create_support_bundle(body: SupportBundleBody, identity: Identity = Depends(get_identity)):
    _require_admin_session(identity)
    return call_daemon("support_bundle.create", identity, include_names=body.include_names)


@api_router.get("/support-bundles")
def list_support_bundles(identity: Identity = Depends(get_identity)):
    _require_admin_session(identity)
    return call_daemon("support_bundle.list", identity)


@api_router.get("/support-bundles/{job_id}")
def get_support_bundle(job_id: int, identity: Identity = Depends(get_identity)):
    _require_admin_session(identity)
    return call_daemon("support_bundle.get", identity, id=job_id)


@api_router.post("/support-bundles/{job_id}/download")
def download_support_bundle(job_id: int, identity: Identity = Depends(get_identity)):
    _require_admin_session(identity)
    result = call_daemon("support_bundle.consume", identity, id=job_id)
    try:
        content = base64.b64decode(result.get("content_base64", ""), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=502, detail="The support bundle payload was invalid") from None
    if len(content) > int(settings.support_bundle_max_bytes):
        raise HTTPException(status_code=502, detail="The support bundle exceeded the download limit")
    filename = result.get("filename")
    if not isinstance(filename, str) or not filename.startswith("boron-support-") or not filename.endswith(".zip"):
        raise HTTPException(status_code=502, detail="The support bundle filename was invalid")
    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
        },
    )
