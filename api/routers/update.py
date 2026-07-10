"""Panel update system: version info + (admin-only) update check/apply/
rollback/history.

`GET /api/v1/version` is authenticated (any role) but deliberately NOT
admin-only: the sidebar footer shows it to customers too. It is not public --
advertising the exact panel version to anonymous scanners makes their job
easier for no benefit (the pre-login page shows the build-time version baked
into the SPA bundle instead, which ships from the same version.py at release
time).

Everything under /api/v1/admin/update/* is admin-only and thin: the daemon
owns all update logic (it's the only process allowed to touch /opt, systemd,
and the GitHub download path); this router just authorizes and forwards.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin
from version import FORGEHOST_VERSION

api_router = APIRouter(prefix="/api/v1", tags=["update"])
admin_api_router = APIRouter(prefix="/api/v1/admin/update", tags=["update"])


@api_router.get("/version")
def get_version(identity: Identity = Depends(get_identity)):
    return {"version": FORGEHOST_VERSION}
