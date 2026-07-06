from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/files", tags=["files"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/files", tags=["ui:files"])


class WriteFileBody(BaseModel):
    path: str
    content: str
    encoding: str = "utf-8"


# Phase 8 feature 13 bodies.
class MovePathsBody(BaseModel):
    paths: list[str]
    dest: str = ""


class CopyBody(BaseModel):
    src: str
    dst: str


class BulkDeleteBody(BaseModel):
    paths: list[str]


class ZipBody(BaseModel):
    paths: list[str]
    archive: str


@api_router.get("")
def list_dir(username: str, path: str = "", identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("file.list", identity, username=username, path=path)


@api_router.get("/content")
def read_file(username: str, path: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("file.read", identity, username=username, path=path)


@api_router.put("/content")
def write_file(username: str, body: WriteFileBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("file.write", identity, username=username, **body.model_dump())


@api_router.post("/mkdir")
def mkdir(username: str, path: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("file.mkdir", identity, username=username, path=path)


@api_router.delete("")
def delete(username: str, path: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("file.delete", identity, username=username, path=path)


# --- Phase 8 feature 13: copy / bulk / zip / search ------------------------


@api_router.post("/copy")
def copy_file(username: str, body: CopyBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("file.copy", identity, username=username, src=body.src, dst=body.dst)


@api_router.post("/bulk-delete")
def bulk_delete(username: str, body: BulkDeleteBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("file.bulk_delete", identity, username=username, paths=body.paths)


@api_router.post("/bulk-move")
def bulk_move(username: str, body: MovePathsBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("file.bulk_move", identity, username=username, paths=body.paths, dest=body.dest)


@api_router.post("/bulk-copy")
def bulk_copy(username: str, body: MovePathsBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("file.bulk_copy", identity, username=username, paths=body.paths, dest=body.dest)


@api_router.post("/zip")
def zip_files(username: str, body: ZipBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("file.zip", identity, username=username, paths=body.paths, archive=body.archive)


@api_router.get("/search")
def search_files(username: str, query: str, mode: str = "name", path: str = "", identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("file.search", identity, username=username, query=query, mode=mode, path=path)


@ui_router.get("")
def ui_browse(request: Request, username: str, path: str = "", identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    listing = call_daemon("file.list", identity, username=username, path=path)
    return templates.TemplateResponse(
        request, "files.html", {"identity": identity, "username": username, "path": path, "entries": listing["entries"]}
    )


@ui_router.post("/mkdir")
def ui_mkdir(username: str, path: str = Form(...), name: str = Form(...), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    new_path = f"{path}/{name}" if path else name
    call_daemon("file.mkdir", identity, username=username, path=new_path)
    return RedirectResponse(f"/ui/accounts/{username}/files?path={path}", status_code=303)


@ui_router.post("/delete")
def ui_delete(username: str, path: str = Form(...), parent: str = Form(""), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("file.delete", identity, username=username, path=path)
    return RedirectResponse(f"/ui/accounts/{username}/files?path={parent}", status_code=303)
