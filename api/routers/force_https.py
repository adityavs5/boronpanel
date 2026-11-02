"""Per-domain "Force HTTPS" (daemon/handlers_force_https.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_domain_access

api_router = APIRouter(prefix="/api/v1/accounts/{username}/domains/{domain}/force-https", tags=["force-https"])


class ForceHttpsBody(BaseModel):
    enabled: bool


@api_router.get("")
def get_force_https(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("force_https.get", identity, username=username, domain=domain)


@api_router.put("")
def set_force_https(username: str, domain: str, body: ForceHttpsBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("force_https.set", identity, username=username, domain=domain, enabled=body.enabled)
