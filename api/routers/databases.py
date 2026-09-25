from __future__ import annotations

import os
import shutil
import tempfile
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access

api_router = APIRouter(prefix="/api/v1/accounts/{username}/databases", tags=["databases"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/databases", tags=["ui:databases"])


class CreateDatabaseBody(BaseModel):
    name: str
    password: str | None = None


class ChangeDatabasePasswordBody(BaseModel):
    password: str | None = None


class RenameDatabaseBody(BaseModel):
    new_name: str


class CreateDatabaseUserBody(BaseModel):
    name: str
    password: str | None = None
    host: str = "localhost"


class DatabaseUserGrantBody(BaseModel):
    user: str
    host: str = "localhost"
    preset: str = "all"
    privileges: list[str] = Field(default_factory=list)


class ChangeDatabaseUserPasswordBody(BaseModel):
    password: str | None = None
    host: str = "localhost"


class RenameDatabaseUserBody(BaseModel):
    new_name: str
    host: str = "localhost"


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


@api_router.patch("/{name}")
def rename_database(username: str, name: str, body: RenameDatabaseBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("db.rename", identity, username=username, name=name, new_name=body.new_name)


@api_router.get("/{name}/export")
def export_database(username: str, name: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    result = call_daemon("db.export", identity, username=username, name=name)
    return FileResponse(result["path"], filename=result["filename"], media_type="application/sql",
        headers={"Cache-Control": "private, no-store"}, background=BackgroundTask(shutil.rmtree, result["cleanup_dir"], True))


@api_router.post("/{name}/import")
def import_database(username: str, name: str, file: UploadFile = File(...), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    fd, path = tempfile.mkstemp(prefix="boron-db-upload-", suffix=".sql", dir="/tmp")
    total = 0
    try:
        with os.fdopen(fd, "wb") as output:
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > 2 * 1024 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="SQL import exceeds the 2 GiB limit")
                output.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="SQL import is empty")
        return call_daemon("db.import", identity, username=username, name=name, path=path)
    finally:
        try: os.unlink(path)
        except FileNotFoundError: pass


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


@api_router.get("/users")
def list_database_users(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("db.user.list", identity, username=username)


@api_router.post("/users")
def create_database_user(username: str, body: CreateDatabaseUserBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("db.user.create", identity, username=username, **body.model_dump(exclude_none=True))


@api_router.patch("/users/{user}/password")
def change_database_user_password(username: str, user: str, body: ChangeDatabaseUserPasswordBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("db.user.change_password", identity, username=username, user=user, **body.model_dump(exclude_none=True))


@api_router.delete("/users/{user}")
def delete_database_user(username: str, user: str, host: str = Query("localhost"), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("db.user.drop", identity, username=username, user=user, host=host)


@api_router.patch("/users/{user}")
def rename_database_user(username: str, user: str, body: RenameDatabaseUserBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("db.user.rename", identity, username=username, user=user, **body.model_dump())


@api_router.post("/{name}/users")
def assign_database_user(username: str, name: str, body: DatabaseUserGrantBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("db.user.grant", identity, username=username, database=name, **body.model_dump())


@api_router.delete("/{name}/users/{user}")
def unassign_database_user(username: str, name: str, user: str, host: str = Query("localhost"), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("db.user.revoke", identity, username=username, database=name, user=user, host=host)


@api_router.post("/{name}/check")
def check_database(username: str, name: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("db.health", identity, username=username, name=name, repair=False)


@api_router.post("/{name}/repair")
def repair_database(username: str, name: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("db.health", identity, username=username, name=name, repair=True)


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
