"""Phase 8 feature 10: process manager.
GET /accounts/{u}/processes · DELETE /accounts/{u}/processes/{pid}."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access

api_router = APIRouter(prefix="/api/v1/accounts/{username}/processes", tags=["processes"])


@api_router.get("")
def list_processes(username: str, identity: Identity = Depends(get_identity)):
    # Admin (any account) + customer (own) -- the daemon strictly scopes to the
    # account's uid regardless.
    require_account_access(identity, username)
    return call_daemon("processes.list", identity, username=username)


@api_router.delete("/{pid}")
def kill_process(username: str, pid: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("processes.kill", identity, username=username, pid=pid)
