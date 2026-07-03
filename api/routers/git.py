from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/git", tags=["git"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/git", tags=["ui:git"])


class CreateRepoBody(BaseModel):
    name: str


class SetDeployTargetBody(BaseModel):
    deploy_target: str


@api_router.get("")
def list_repos(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("git.repo.list", identity, username=username)


@api_router.post("")
def create_repo(username: str, body: CreateRepoBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("git.repo.create", identity, username=username, name=body.name)


@api_router.delete("/{name}")
def delete_repo(username: str, name: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("git.repo.delete", identity, username=username, name=name)


@api_router.patch("/{name}/deploy-target")
def set_deploy_target(username: str, name: str, body: SetDeployTargetBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("git.repo.set_deploy_target", identity, username=username, name=name, deploy_target=body.deploy_target)


@api_router.get("/{name}/push-log")
def get_push_log(username: str, name: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("git.repo.push_log", identity, username=username, name=name)


# --- UI ---------------------------------------------------------------------


@ui_router.get("")
def ui_git_home(request: Request, username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    repos = call_daemon("git.repo.list", identity, username=username)["repos"]
    push_logs = {}
    for r in repos:
        push_logs[r["name"]] = call_daemon("git.repo.push_log", identity, username=username, name=r["name"])["lines"]
    return templates.TemplateResponse(
        request, "git_repos.html", {"identity": identity, "username": username, "repos": repos, "push_logs": push_logs},
    )


@ui_router.post("")
def ui_create_repo(username: str, name: str = Form(...), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("git.repo.create", identity, username=username, name=name)
    return RedirectResponse(f"/ui/accounts/{username}/git", status_code=303)


@ui_router.post("/{name}/deploy-target")
def ui_set_deploy_target(username: str, name: str, deploy_target: str = Form(...), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("git.repo.set_deploy_target", identity, username=username, name=name, deploy_target=deploy_target)
    return RedirectResponse(f"/ui/accounts/{username}/git", status_code=303)


@ui_router.post("/{name}/delete")
def ui_delete_repo(username: str, name: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("git.repo.delete", identity, username=username, name=name)
    return RedirectResponse(f"/ui/accounts/{username}/git", status_code=303)
