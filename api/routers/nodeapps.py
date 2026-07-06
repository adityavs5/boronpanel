"""Phase 7a feature 1: NodeJS app hosting. Account-scoped collection
(`/accounts/{u}/apps/node`, the goal's own explicit API shape) -- each app
additionally binds to one of the account's own domains at create time
(daemon/nodeapps.py enforces the actual ownership/uniqueness checks; this
router only wires HTTP verbs to RPC ops, same as every other router in
this project).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from shared.config import settings

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/apps/node", tags=["apps:node"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/apps/node", tags=["ui:apps:node"])


class CreateNodeAppBody(BaseModel):
    domain: str
    name: str
    entry_point: str
    node_version: str | None = None
    env_vars: dict[str, str] = {}


class UpdateNodeAppBody(BaseModel):
    entry_point: str | None = None
    node_version: str | None = None
    env_vars: dict[str, str] | None = None


def _parse_env_text(text: str) -> dict[str, str]:
    """UI convenience form: one KEY=VALUE per line -- lines without '=' or
    starting with '#' are ignored, matching the shape of every other
    plain-text env/config editor already in this project (e.g. cron
    MAILTO's own simple line format)."""
    env: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def _env_to_text(env: dict[str, str]) -> str:
    return "\n".join(f"{k}={v}" for k, v in (env or {}).items())


@api_router.get("")
def list_node_apps(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.node.list", identity, username=username)


@api_router.post("")
def create_node_app(username: str, body: CreateNodeAppBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.node.create", identity, username=username, **body.model_dump())


@api_router.get("/{app_id}")
def get_node_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.node.get", identity, username=username, id=app_id)


@api_router.patch("/{app_id}")
def update_node_app(username: str, app_id: int, body: UpdateNodeAppBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.node.update", identity, username=username, id=app_id, **body.model_dump(exclude_none=True))


@api_router.delete("/{app_id}")
def delete_node_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.node.delete", identity, username=username, id=app_id)


@api_router.post("/{app_id}/start")
def start_node_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.node.start", identity, username=username, id=app_id)


@api_router.post("/{app_id}/stop")
def stop_node_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.node.stop", identity, username=username, id=app_id)


@api_router.post("/{app_id}/restart")
def restart_node_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.node.restart", identity, username=username, id=app_id)


@api_router.post("/{app_id}/npm-install")
def npm_install_node_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.node.npm_install", identity, username=username, id=app_id)


@api_router.get("/{app_id}/logs")
def get_node_app_logs(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.node.logs", identity, username=username, id=app_id)


# --- server-rendered UI -------------------------------------------------


@ui_router.get("")
def ui_node_apps_home(request: Request, username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    apps = call_daemon("apps.node.list", identity, username=username)["apps"]
    domains = call_daemon("domain.list", identity, username=username)["domains"]
    return templates.TemplateResponse(
        request,
        "node_apps.html",
        {
            "identity": identity,
            "username": username,
            "apps": apps,
            "domains": domains,
            "node_versions": settings.node_versions,
            "default_node_version": settings.default_node_version,
        },
    )


@ui_router.post("")
def ui_create_node_app(
    username: str,
    domain: str = Form(...),
    name: str = Form(...),
    entry_point: str = Form(...),
    node_version: str = Form(...),
    env_vars_text: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    call_daemon(
        "apps.node.create", identity, username=username, domain=domain, name=name,
        entry_point=entry_point, node_version=node_version, env_vars=_parse_env_text(env_vars_text),
    )
    return RedirectResponse(f"/ui/accounts/{username}/apps/node", status_code=303)


@ui_router.get("/{app_id}")
def ui_node_app_detail(request: Request, username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    app = call_daemon("apps.node.get", identity, username=username, id=app_id)
    logs = call_daemon("apps.node.logs", identity, username=username, id=app_id)["log_lines"]
    return templates.TemplateResponse(
        request,
        "node_app_detail.html",
        {
            "identity": identity,
            "username": username,
            "app": app,
            "env_vars_text": _env_to_text(app["env_vars"]),
            "log_lines": logs,
            "node_versions": settings.node_versions,
        },
    )


@ui_router.post("/{app_id}")
def ui_update_node_app(
    username: str,
    app_id: int,
    entry_point: str = Form(...),
    node_version: str = Form(...),
    env_vars_text: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    call_daemon(
        "apps.node.update", identity, username=username, id=app_id,
        entry_point=entry_point, node_version=node_version, env_vars=_parse_env_text(env_vars_text),
    )
    return RedirectResponse(f"/ui/accounts/{username}/apps/node/{app_id}", status_code=303)


@ui_router.post("/{app_id}/start")
def ui_start_node_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("apps.node.start", identity, username=username, id=app_id)
    return RedirectResponse(f"/ui/accounts/{username}/apps/node/{app_id}", status_code=303)


@ui_router.post("/{app_id}/stop")
def ui_stop_node_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("apps.node.stop", identity, username=username, id=app_id)
    return RedirectResponse(f"/ui/accounts/{username}/apps/node/{app_id}", status_code=303)


@ui_router.post("/{app_id}/restart")
def ui_restart_node_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("apps.node.restart", identity, username=username, id=app_id)
    return RedirectResponse(f"/ui/accounts/{username}/apps/node/{app_id}", status_code=303)


@ui_router.post("/{app_id}/npm-install")
def ui_npm_install_node_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("apps.node.npm_install", identity, username=username, id=app_id)
    return RedirectResponse(f"/ui/accounts/{username}/apps/node/{app_id}", status_code=303)


@ui_router.post("/{app_id}/delete")
def ui_delete_node_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("apps.node.delete", identity, username=username, id=app_id)
    return RedirectResponse(f"/ui/accounts/{username}/apps/node", status_code=303)
