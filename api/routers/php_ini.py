from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access

api_router = APIRouter(prefix="/api/v1/accounts/{username}/php-ini", tags=["php-ini"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/php-ini", tags=["ui:php-ini"])


class SetPhpIniBody(BaseModel):
    memory_limit: str | None = None
    upload_max_filesize: str | None = None
    post_max_size: str | None = None
    max_execution_time: int | None = None
    display_errors: bool | None = None
    error_reporting: str | None = None


@api_router.get("")
def get_php_ini(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("php_ini.get", identity, username=username)


@api_router.patch("")
def set_php_ini(username: str, body: SetPhpIniBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("php_ini.set", identity, username=username, **body.model_dump(exclude_none=True))


@api_router.delete("")
def reset_php_ini(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("php_ini.reset", identity, username=username)


@ui_router.post("")
def ui_set_php_ini(
    username: str,
    memory_limit: str = Form(...),
    upload_max_filesize: str = Form(...),
    post_max_size: str = Form(...),
    max_execution_time: int = Form(...),
    display_errors: str = Form(""),
    error_reporting: str = Form(...),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    call_daemon(
        "php_ini.set",
        identity,
        username=username,
        memory_limit=memory_limit,
        upload_max_filesize=upload_max_filesize,
        post_max_size=post_max_size,
        max_execution_time=max_execution_time,
        display_errors=(display_errors == "on"),
        error_reporting=error_reporting,
    )
    return RedirectResponse(f"/ui/accounts/{username}", status_code=303)


@ui_router.post("/reset")
def ui_reset_php_ini(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("php_ini.reset", identity, username=username)
    return RedirectResponse(f"/ui/accounts/{username}", status_code=303)
