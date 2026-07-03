from __future__ import annotations

from fastapi import APIRouter, Depends
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/disk-tree", tags=["disktree"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/disk-tree", tags=["ui:disktree"])


@api_router.get("")
def get_disk_tree(username: str, path: str = "", identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("disktree.get", identity, username=username, path=path)


@api_router.get("/top-files")
def get_top_files(username: str, path: str = "", identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("disktree.top_files", identity, username=username, path=path)


@ui_router.get("")
def ui_disk_tree_home(request: Request, username: str, path: str = "", identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    tree = call_daemon("disktree.get", identity, username=username, path=path)
    top_files = call_daemon("disktree.top_files", identity, username=username, path="")["files"]
    return templates.TemplateResponse(
        request, "disk_tree.html", {"identity": identity, "username": username, "tree": tree, "top_files": top_files},
    )
