"""Phase 8 features 8 & 9: WP-CLI + Composer (async, run as the account user).

  GET  /api/v1/accounts/{u}/wordpress/detect
  POST /api/v1/accounts/{u}/wordpress/{id}/wpcli
  GET  /api/v1/accounts/{u}/wordpress/wpcli/runs[/{job_id}]
  POST /api/v1/accounts/{u}/composer
  GET  /api/v1/accounts/{u}/composer/runs[/{job_id}]
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access

wpcli_router = APIRouter(prefix="/api/v1/accounts/{username}/wordpress", tags=["wpcli"])
composer_router = APIRouter(prefix="/api/v1/accounts/{username}/composer", tags=["composer"])


@wpcli_router.get("/detect")
def detect(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("wpcli.detect", identity, username=username)


class WpcliBody(BaseModel):
    action: str
    # optional per-action fields
    name: str | None = None
    all: bool = False
    user: str | None = None
    search: str | None = None
    replace: str | None = None
    preview: bool = True


@wpcli_router.post("/{wp_id}/wpcli")
def run_wpcli(username: str, wp_id: str, body: WpcliBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    # wp_id is the install's domain (see wpcli.detect_installs).
    return call_daemon("wpcli.run", identity, username=username, domain=wp_id, **body.model_dump(exclude_none=True))


@wpcli_router.get("/wpcli/runs")
def list_wpcli_runs(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("wpcli.list", identity, username=username)


@wpcli_router.get("/wpcli/runs/{job_id}")
def get_wpcli_run(username: str, job_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("wpcli.get", identity, username=username, job_id=job_id)


class ComposerBody(BaseModel):
    command: str  # install | update | require | dump-autoload
    app_dir: str = ""  # relative to the account home
    package: str | None = None  # for require


@composer_router.post("")
def run_composer(username: str, body: ComposerBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("composer.run", identity, username=username, **body.model_dump(exclude_none=True))


@composer_router.get("/runs")
def list_composer_runs(username: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("composer.list", identity, username=username)


@composer_router.get("/runs/{job_id}")
def get_composer_run(username: str, job_id: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("composer.get", identity, username=username, job_id=job_id)
