"""Administrator search for the API's correlated HTTP errors.

Reads the API-owned SQLite search index beside ``api-error.log`` and returns
bounded newest-first results. Admin-only: it exposes internal paths, timings
and usernames of failed requests, which is operator information.

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
def get_error_log(
    limit: int = 100,
    reference: str = "",
    request_id: str = "",
    date_from: str = "",
    date_to: str = "",
    status: int | None = None,
    identity: Identity = Depends(get_identity),
):
    require_admin(identity)
    # Clamp: the goal asks for "last 100"; allow a smaller page but never an
    # unbounded read that could try to parse a huge backlog into one response.
    limit = max(1, min(limit, 100))
    return {
        "errors": logsetup.search_error_records(
            limit=limit,
            reference=reference,
            request_id=request_id,
            date_from=date_from,
            date_to=date_to,
            status=status,
        )
    }
