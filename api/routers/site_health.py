from fastapi import APIRouter, Depends, Query

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access

api_router = APIRouter(prefix="/api/v1/accounts/{username}/site-health", tags=["site-health"])


@api_router.get("")
def site_health(username: str, domain: str | None = Query(default=None), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("site_health.check", identity, username=username, domain=domain)
