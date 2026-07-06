"""Phase 7a feature 2: Python WSGI/ASGI app hosting. Same shape as
api/routers/nodeapps.py -- account-scoped collection
(`/accounts/{u}/apps/python`, the goal's own explicit API shape).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/apps/python", tags=["apps:python"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/apps/python", tags=["ui:apps:python"])


class CreatePythonAppBody(BaseModel):
    domain: str
    name: str
    entry_point: str
    app_type: str = "wsgi"
    env_vars: dict[str, str] = {}


class UpdatePythonAppBody(BaseModel):
    entry_point: str | None = None
    app_type: str | None = None
    env_vars: dict[str, str] | None = None


def _parse_env_text(text: str) -> dict[str, str]:
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
def list_python_apps(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.python.list", identity, username=username)


@api_router.post("")
def create_python_app(username: str, body: CreatePythonAppBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.python.create", identity, username=username, **body.model_dump())


@api_router.get("/{app_id}")
def get_python_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.python.get", identity, username=username, id=app_id)


@api_router.patch("/{app_id}")
def update_python_app(username: str, app_id: int, body: UpdatePythonAppBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.python.update", identity, username=username, id=app_id, **body.model_dump(exclude_none=True))


@api_router.delete("/{app_id}")
def delete_python_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.python.delete", identity, username=username, id=app_id)


@api_router.post("/{app_id}/start")
def start_python_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.python.start", identity, username=username, id=app_id)


@api_router.post("/{app_id}/stop")
def stop_python_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.python.stop", identity, username=username, id=app_id)


@api_router.post("/{app_id}/restart")
def restart_python_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.python.restart", identity, username=username, id=app_id)


@api_router.post("/{app_id}/pip-install")
def pip_install_python_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.python.pip_install", identity, username=username, id=app_id)


@api_router.get("/{app_id}/logs")
def get_python_app_logs(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("apps.python.logs", identity, username=username, id=app_id)


# --- server-rendered UI -------------------------------------------------


@ui_router.get("")
def ui_python_apps_home(request: Request, username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    apps = call_daemon("apps.python.list", identity, username=username)["apps"]
    domains = call_daemon("domain.list", identity, username=username)["domains"]
    return templates.TemplateResponse(
        request, "python_apps.html", {"identity": identity, "username": username, "apps": apps, "domains": domains},
    )


@ui_router.post("")
def ui_create_python_app(
    username: str,
    domain: str = Form(...),
    name: str = Form(...),
    entry_point: str = Form(...),
    app_type: str = Form("wsgi"),
    env_vars_text: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    call_daemon(
        "apps.python.create", identity, username=username, domain=domain, name=name,
        entry_point=entry_point, app_type=app_type, env_vars=_parse_env_text(env_vars_text),
    )
    return RedirectResponse(f"/ui/accounts/{username}/apps/python", status_code=303)


@ui_router.get("/{app_id}")
def ui_python_app_detail(request: Request, username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    app = call_daemon("apps.python.get", identity, username=username, id=app_id)
    logs = call_daemon("apps.python.logs", identity, username=username, id=app_id)["log_lines"]
    return templates.TemplateResponse(
        request,
        "python_app_detail.html",
        {"identity": identity, "username": username, "app": app, "env_vars_text": _env_to_text(app["env_vars"]), "log_lines": logs},
    )


@ui_router.post("/{app_id}")
def ui_update_python_app(
    username: str,
    app_id: int,
    entry_point: str = Form(...),
    app_type: str = Form(...),
    env_vars_text: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    call_daemon(
        "apps.python.update", identity, username=username, id=app_id,
        entry_point=entry_point, app_type=app_type, env_vars=_parse_env_text(env_vars_text),
    )
    return RedirectResponse(f"/ui/accounts/{username}/apps/python/{app_id}", status_code=303)


@ui_router.post("/{app_id}/start")
def ui_start_python_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("apps.python.start", identity, username=username, id=app_id)
    return RedirectResponse(f"/ui/accounts/{username}/apps/python/{app_id}", status_code=303)


@ui_router.post("/{app_id}/stop")
def ui_stop_python_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("apps.python.stop", identity, username=username, id=app_id)
    return RedirectResponse(f"/ui/accounts/{username}/apps/python/{app_id}", status_code=303)


@ui_router.post("/{app_id}/restart")
def ui_restart_python_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("apps.python.restart", identity, username=username, id=app_id)
    return RedirectResponse(f"/ui/accounts/{username}/apps/python/{app_id}", status_code=303)


@ui_router.post("/{app_id}/pip-install")
def ui_pip_install_python_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("apps.python.pip_install", identity, username=username, id=app_id)
    return RedirectResponse(f"/ui/accounts/{username}/apps/python/{app_id}", status_code=303)


@ui_router.post("/{app_id}/delete")
def ui_delete_python_app(username: str, app_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("apps.python.delete", identity, username=username, id=app_id)
    return RedirectResponse(f"/ui/accounts/{username}/apps/python", status_code=303)
