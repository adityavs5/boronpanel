from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access

api_router = APIRouter(prefix="/api/v1/accounts/{username}/php-ini", tags=["php-ini"])
# Extensions are their own resource, not an ini setting -- own prefix, own
# router object (registered separately in api/main.py's extra-router block).
ext_api_router = APIRouter(prefix="/api/v1/accounts/{username}/php-extensions", tags=["php-extensions"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/php-ini", tags=["ui:php-ini"])


class SetPhpIniBody(BaseModel):
    memory_limit: str | None = None
    upload_max_filesize: str | None = None
    post_max_size: str | None = None
    max_execution_time: int | None = None
    display_errors: bool | None = None
    error_reporting: str | None = None
    # Uniform transport for any registry directive ("max_input_vars",
    # "session.gc_maxlifetime", ... -- dotted names can't be model fields
    # or RPC kwargs). A None value means "revert that directive to its
    # default". The daemon validates names/values against its registry.
    directives: dict[str, str | int | bool | None] | None = None


class SetPhpExtensionsBody(BaseModel):
    enabled: list[str]


@api_router.get("")
def get_php_ini(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("php_ini.get", identity, username=username)


@api_router.patch("")
def set_php_ini(username: str, body: SetPhpIniBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    # exclude_none would also strip the *nested* meaning of None inside
    # `directives` if applied naively -- it only strips top-level unset
    # fields here; the directives dict travels as-is.
    params = {k: v for k, v in body.model_dump().items() if v is not None}
    return call_daemon("php_ini.set", identity, username=username, **params)


@api_router.delete("")
def reset_php_ini(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("php_ini.reset", identity, username=username)


# Self-service like the ini overrides above: which PHP extensions load for
# your own code is a customer-managed setting in cPanel-equivalent panels
# (MultiPHP-style), not an operator action.
@ext_api_router.get("")
def list_php_extensions(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("php_ext.list", identity, username=username)


@ext_api_router.put("")
def set_php_extensions(username: str, body: SetPhpExtensionsBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("php_ext.set", identity, username=username, enabled=body.enabled)


@ext_api_router.delete("")
def reset_php_extensions(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("php_ext.reset", identity, username=username)


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
