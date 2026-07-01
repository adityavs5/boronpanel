from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access

api_router = APIRouter(prefix="/api/v1/accounts/{username}/databases", tags=["databases"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/databases", tags=["ui:databases"])


class CreateDatabaseBody(BaseModel):
    name: str
    password: str | None = None


class ChangeDatabasePasswordBody(BaseModel):
    password: str | None = None


@api_router.get("")
def list_databases(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("db.list", identity, username=username)


@api_router.post("")
def create_database(username: str, body: CreateDatabaseBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("db.create", identity, username=username, **body.model_dump(exclude_none=True))


@api_router.delete("/{name}")
def drop_database(username: str, name: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("db.drop", identity, username=username, name=name)


@api_router.post("/{name}/password")
def change_password(username: str, name: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("db.change_password", identity, username=username, name=name)


@api_router.patch("/{name}/password")
def change_password_patch(username: str, name: str, body: ChangeDatabasePasswordBody, identity: Identity = Depends(get_identity)):
    """Phase 3 feature 10: the goal's literal password-manager API shape
    is PATCH (the existing POST route above predates this feature and
    is kept as-is for backward compatibility) -- same handler either
    way, an explicit customer-supplied password is optional on both
    (falls back to a fresh generated one if omitted)."""
    require_account_access(identity, username)
    params = {"username": username, "name": name}
    if body.password:
        params["password"] = body.password
    return call_daemon("db.change_password", identity, **params)


@ui_router.post("")
def ui_create_database(username: str, name: str = Form(...), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("db.create", identity, username=username, name=name)
    return RedirectResponse(f"/ui/accounts/{username}", status_code=303)


@ui_router.post("/{name}/delete")
def ui_drop_database(username: str, name: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("db.drop", identity, username=username, name=name)
    return RedirectResponse(f"/ui/accounts/{username}", status_code=303)


@ui_router.post("/{name}/password")
def ui_change_password(username: str, name: str, password: str = Form(...), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("db.change_password", identity, username=username, name=name, password=password)
    return RedirectResponse(f"/ui/accounts/{username}", status_code=303)
