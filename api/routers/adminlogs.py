"""Run A feature 7: admin view of the API's own recent 5xx errors.

Reads `api-error.log` (written by this same forgehost-api process, so no
daemon RPC is needed -- the file is ours) and returns the last N records,
newest first. Admin-only: it exposes internal paths/timings/usernames of
failed requests, which is operator information, not customer information.

Distinct from api/routers/logs_router.py, which serves a *hosting
account's* own OLS/PHP logs under /accounts/{username}/logs -- this is the
panel's own request log, a server-wide admin resource.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api import logsetup
from api.security import Identity, get_identity, require_admin

api_router = APIRouter(prefix="/api/v1/admin/logs", tags=["admin-logs"])


@api_router.get("/errors")
def get_error_log(limit: int = 100, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    # Clamp: the goal asks for "last 100"; allow a smaller page but never an
    # unbounded read that could try to parse a huge backlog into one response.
    limit = max(1, min(limit, 100))
    return {"errors": logsetup.tail_error_records(limit=limit)}
