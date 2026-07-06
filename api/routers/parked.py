"""Phase 8 feature 3: parked (alias) domains. CRUD /accounts/{u}/parked-domains."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access

api_router = APIRouter(prefix="/api/v1/accounts/{username}/parked-domains", tags=["parked-domains"])


class AddParkedBody(BaseModel):
    parked_domain: str
    # Defaults to the account's primary domain when omitted.
    target_domain: str | None = None


@api_router.get("")
def list_parked(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("parked.list", identity, username=username)


@api_router.post("")
def add_parked(username: str, body: AddParkedBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon(
        "parked.add", identity, username=username,
        parked_domain=body.parked_domain, target_domain=body.target_domain,
    )


@api_router.delete("/{parked_domain}")
def remove_parked(username: str, parked_domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("parked.remove", identity, username=username, parked_domain=parked_domain)
