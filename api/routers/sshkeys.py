from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_customer_self_access
from api.templates import templates

# Phase 4 feature 6: "Customer panel only -- admin cannot see account SSH
# keys" is this feature's own explicit, literal scoping -- every route here
# uses require_customer_self_access, not require_account_access, so an
# admin identity is rejected even for an account they'd otherwise be
# allowed to manage.
api_router = APIRouter(prefix="/api/v1/accounts/{username}/ssh-keys", tags=["sshkeys"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/ssh-keys", tags=["ui:sshkeys"])


class AddKeyBody(BaseModel):
    key: str


@api_router.get("")
def list_keys(username: str, identity: Identity = Depends(get_identity)):
    require_customer_self_access(identity, username)
    return call_daemon("sshkeys.list", identity, username=username)


@api_router.post("")
def add_key(username: str, body: AddKeyBody, identity: Identity = Depends(get_identity)):
    require_customer_self_access(identity, username)
    return call_daemon("sshkeys.add", identity, username=username, key=body.key)


@api_router.delete("/{fingerprint}")
def delete_key(username: str, fingerprint: str, identity: Identity = Depends(get_identity)):
    require_customer_self_access(identity, username)
    return call_daemon("sshkeys.delete", identity, username=username, fingerprint=fingerprint)


# --- UI ---------------------------------------------------------------------


@ui_router.get("")
def ui_ssh_keys_home(request: Request, username: str, identity: Identity = Depends(get_identity)):
    require_customer_self_access(identity, username)
    keys = call_daemon("sshkeys.list", identity, username=username)["keys"]
    return templates.TemplateResponse(request, "ssh_keys.html", {"identity": identity, "username": username, "keys": keys})


@ui_router.post("")
def ui_add_key(username: str, key: str = Form(...), identity: Identity = Depends(get_identity)):
    require_customer_self_access(identity, username)
    call_daemon("sshkeys.add", identity, username=username, key=key)
    return RedirectResponse(f"/ui/accounts/{username}/ssh-keys", status_code=303)


@ui_router.post("/delete")
def ui_delete_key(username: str, fingerprint: str = Form(...), identity: Identity = Depends(get_identity)):
    require_customer_self_access(identity, username)
    call_daemon("sshkeys.delete", identity, username=username, fingerprint=fingerprint)
    return RedirectResponse(f"/ui/accounts/{username}/ssh-keys", status_code=303)
