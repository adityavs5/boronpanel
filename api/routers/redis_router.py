"""Phase 7a feature 3: per-account Redis. Singular account-scoped resource
(`/accounts/{u}/redis`, the goal's own explicit API shape -- no {id}, since
there's at most one Redis instance per account)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/redis", tags=["redis"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/redis", tags=["ui:redis"])


class EnableRedisBody(BaseModel):
    mem_mb: int | None = None


class SetMemLimitBody(BaseModel):
    mem_mb: int


@api_router.get("")
def get_redis_status(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("redis.status", identity, username=username)


@api_router.post("")
def enable_redis(username: str, body: EnableRedisBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("redis.enable", identity, username=username, **body.model_dump(exclude_none=True))


@api_router.patch("")
def set_redis_mem_limit(username: str, body: SetMemLimitBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("redis.set_mem_limit", identity, username=username, mem_mb=body.mem_mb)


@api_router.delete("")
def disable_redis(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("redis.disable", identity, username=username)


@api_router.post("/flush")
def flush_redis(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("redis.flush", identity, username=username)


@api_router.get("/connection-info")
def get_redis_connection_info(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("redis.connection_info", identity, username=username)


# --- server-rendered UI -------------------------------------------------


@ui_router.get("")
def ui_redis_home(request: Request, username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    status = call_daemon("redis.status", identity, username=username)
    connection_info = call_daemon("redis.connection_info", identity, username=username) if status.get("provisioned") else None
    return templates.TemplateResponse(
        request, "redis_detail.html", {"identity": identity, "username": username, "status": status, "connection_info": connection_info},
    )


@ui_router.post("")
def ui_enable_redis(username: str, mem_mb: int = Form(64), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("redis.enable", identity, username=username, mem_mb=mem_mb)
    return RedirectResponse(f"/ui/accounts/{username}/redis", status_code=303)


@ui_router.post("/mem-limit")
def ui_set_mem_limit(username: str, mem_mb: int = Form(...), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("redis.set_mem_limit", identity, username=username, mem_mb=mem_mb)
    return RedirectResponse(f"/ui/accounts/{username}/redis", status_code=303)


@ui_router.post("/flush")
def ui_flush_redis(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("redis.flush", identity, username=username)
    return RedirectResponse(f"/ui/accounts/{username}/redis", status_code=303)


@ui_router.post("/disable")
def ui_disable_redis(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("redis.disable", identity, username=username)
    return RedirectResponse(f"/ui/accounts/{username}/redis", status_code=303)
