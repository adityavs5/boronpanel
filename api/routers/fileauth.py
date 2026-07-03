from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/file-auth", tags=["fileauth"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/file-auth", tags=["ui:fileauth"])


class EnableBody(BaseModel):
    path: str


class AddUserBody(BaseModel):
    path: str
    htuser: str
    password: str


# --- JSON API (goal's literal shape: CRUD /accounts/{u}/file-auth) --------


@api_router.get("")
def list_protected_dirs(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("fileauth.list", identity, username=username)


@api_router.post("")
def enable_protection(username: str, body: EnableBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("fileauth.enable", identity, username=username, path=body.path)


@api_router.delete("")
def disable_protection(username: str, path: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("fileauth.disable", identity, username=username, path=path)


@api_router.get("/users")
def list_users(username: str, path: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("fileauth.user.list", identity, username=username, path=path)


@api_router.post("/users")
def add_user(username: str, body: AddUserBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("fileauth.user.add", identity, username=username, **body.model_dump())


@api_router.delete("/users")
def delete_user(username: str, path: str, htuser: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("fileauth.user.delete", identity, username=username, path=path, htuser=htuser)


# --- UI ---------------------------------------------------------------------


@ui_router.get("")
def ui_file_auth_home(request: Request, username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    protected = call_daemon("fileauth.list", identity, username=username)["protected"]
    users_by_dir = {}
    for entry in protected:
        users_by_dir[entry["path"]] = call_daemon("fileauth.user.list", identity, username=username, path=entry["path"])["users"]
    return templates.TemplateResponse(
        request, "file_auth.html",
        {"identity": identity, "username": username, "protected": protected, "users_by_dir": users_by_dir},
    )


@ui_router.post("/enable")
def ui_enable_protection(username: str, path: str = Form(...), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("fileauth.enable", identity, username=username, path=path)
    return RedirectResponse(f"/ui/accounts/{username}/file-auth", status_code=303)


@ui_router.post("/disable")
def ui_disable_protection(username: str, path: str = Form(...), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("fileauth.disable", identity, username=username, path=path)
    return RedirectResponse(f"/ui/accounts/{username}/file-auth", status_code=303)


@ui_router.post("/users/add")
def ui_add_user(
    username: str,
    path: str = Form(...),
    htuser: str = Form(...),
    password: str = Form(...),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    call_daemon("fileauth.user.add", identity, username=username, path=path, htuser=htuser, password=password)
    return RedirectResponse(f"/ui/accounts/{username}/file-auth", status_code=303)


@ui_router.post("/users/delete")
def ui_delete_user(username: str, path: str = Form(...), htuser: str = Form(...), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("fileauth.user.delete", identity, username=username, path=path, htuser=htuser)
    return RedirectResponse(f"/ui/accounts/{username}/file-auth", status_code=303)
