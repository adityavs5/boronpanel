"""Phase 8 feature 4: whole-domain forwarding.
CRUD /accounts/{u}/domains/{d}/forwarding."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_domain_access

api_router = APIRouter(prefix="/api/v1/accounts/{username}/domains/{domain}/forwarding", tags=["forwarding"])


class ForwardingBody(BaseModel):
    target_url: str
    status_code: int = 301
    keep_path: bool = True


@api_router.get("")
def get_forwarding(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("forwarding.get", identity, username=username, domain=domain)


@api_router.put("")
def set_forwarding(username: str, domain: str, body: ForwardingBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon(
        "forwarding.set", identity, username=username, domain=domain,
        target_url=body.target_url, status_code=body.status_code, keep_path=body.keep_path,
    )


@api_router.delete("")
def delete_forwarding(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("forwarding.delete", identity, username=username, domain=domain)
