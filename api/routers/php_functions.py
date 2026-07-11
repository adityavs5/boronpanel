"""QA round 2, item 9: admin-only PHP disable_functions overrides, per-account
or per-domain, layered on the system-wide hardened default. Deliberately
admin-only (require_admin, not require_account_access) -- a separate surface
from the customer-facing php-ini overrides (api/routers/php_ini.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

api_router = APIRouter(prefix="/api/v1/admin/accounts/{username}/php-functions", tags=["php-functions"])


class SetOverrideBody(BaseModel):
    domain: str | None = None  # None = account-wide
    disable_functions: list[str] = []


@api_router.get("")
def get_overrides(username: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("php_functions.get", identity, username=username)


@api_router.put("")
def set_override(username: str, body: SetOverrideBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon(
        "php_functions.set", identity, username=username, domain=body.domain, disable_functions=body.disable_functions
    )


@api_router.delete("")
def delete_override(username: str, domain: str | None = None, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    return call_daemon("php_functions.delete", identity, username=username, domain=domain)
