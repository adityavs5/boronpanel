from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/ftp", tags=["ftp"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/ftp", tags=["ui:ftp"])


class CreateFtpAccountBody(BaseModel):
    label: str
    password: str
    path: str = ""


class SetPathBody(BaseModel):
    path: str


class ChangePasswordBody(BaseModel):
    password: str


@api_router.get("")
def list_ftp_accounts(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("ftp.list", identity, username=username)


@api_router.post("")
def create_ftp_account(username: str, body: CreateFtpAccountBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("ftp.create", identity, username=username, **body.model_dump())


@api_router.patch("/{label}")
def set_ftp_path(username: str, label: str, body: SetPathBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("ftp.set_path", identity, username=username, label=label, path=body.path)


@api_router.patch("/{label}/password")
def change_ftp_password(username: str, label: str, body: ChangePasswordBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("ftp.change_password", identity, username=username, label=label, password=body.password)


@api_router.delete("/{label}")
def delete_ftp_account(username: str, label: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("ftp.delete", identity, username=username, label=label)


@ui_router.get("")
def ui_ftp_home(request: Request, username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    accounts = call_daemon("ftp.list", identity, username=username)["ftp_accounts"]
    return templates.TemplateResponse(request, "ftp_accounts.html", {"identity": identity, "username": username, "ftp_accounts": accounts})


@ui_router.post("")
def ui_create_ftp_account(
    username: str,
    label: str = Form(...),
    password: str = Form(...),
    path: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    call_daemon("ftp.create", identity, username=username, label=label, password=password, path=path)
    return RedirectResponse(f"/ui/accounts/{username}/ftp", status_code=303)


@ui_router.post("/{label}/path")
def ui_set_ftp_path(username: str, label: str, path: str = Form(...), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("ftp.set_path", identity, username=username, label=label, path=path)
    return RedirectResponse(f"/ui/accounts/{username}/ftp", status_code=303)


@ui_router.post("/{label}/password")
def ui_change_ftp_password(username: str, label: str, password: str = Form(...), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("ftp.change_password", identity, username=username, label=label, password=password)
    return RedirectResponse(f"/ui/accounts/{username}/ftp", status_code=303)


@ui_router.post("/{label}/delete")
def ui_delete_ftp_account(username: str, label: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("ftp.delete", identity, username=username, label=label)
    return RedirectResponse(f"/ui/accounts/{username}/ftp", status_code=303)
