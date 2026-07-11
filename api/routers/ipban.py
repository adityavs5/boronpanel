"""QA round 2, item 14: permanent, server-wide IP/CIDR bans. Admin-only,
host-wide -- distinct from the per-account/per-domain IP blocker
(api/routers/ipblock.py) and from fail2ban's automatic jails
(api/routers/fail2ban.py, unban-only)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

api_router = APIRouter(prefix="/api/v1/admin/ip-bans", tags=["ipban"])


class AddBanBody(BaseModel):
    value: str
    reason: str = ""


@api_router.get("")
def list_bans(identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("ipban.list", identity)


@api_router.post("")
def add_ban(body: AddBanBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    # author = the acting admin; recorded server-side, never taken from input.
    return call_daemon("ipban.add", identity, value=body.value, reason=body.reason, actor=identity.username)


@api_router.delete("/{ban_id}")
def delete_ban(ban_id: int, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("ipban.delete", identity, id=ban_id)
