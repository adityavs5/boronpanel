from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access

api_router = APIRouter(prefix="/api/v1/accounts/{username}/databases", tags=["pma"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/databases", tags=["ui:pma"])


@api_router.post("/{name}/pma-token")
def create_pma_token(username: str, name: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("pma.token.create", identity, username=username, name=name)


@ui_router.post("/{name}/pma-token")
def ui_create_pma_token(username: str, name: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    result = call_daemon("pma.token.create", identity, username=username, name=name)
    if not result.get("pma_url"):
        # pma_hostname not configured -- nothing to redirect to.
        return RedirectResponse(f"/ui/accounts/{username}", status_code=303)
    return RedirectResponse(result["pma_url"], status_code=303)
