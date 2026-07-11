"""Missing-features batch, goal feature 3: wildcard domains.
PATCH /accounts/{u}/domains/{d}/wildcard (the goal's own literal shape)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_domain_access

api_router = APIRouter(prefix="/api/v1/accounts/{username}/domains/{domain}/wildcard", tags=["wildcard"])


class WildcardBody(BaseModel):
    enabled: bool = True


@api_router.get("")
def get_wildcard(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("wildcard.get", identity, domain=domain)


@api_router.patch("")
def set_wildcard(username: str, domain: str, body: WildcardBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("wildcard.set", identity, domain=domain, **body.model_dump())
