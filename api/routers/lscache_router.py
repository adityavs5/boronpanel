"""Phase 7a feature 4: LSCache. Domain-scoped
(`/accounts/{u}/domains/{d}/lscache`, the goal's own explicit API shape)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_domain_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/domains/{domain}/lscache", tags=["lscache"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/domains/{domain}/lscache", tags=["ui:lscache"])


class SetLscacheBody(BaseModel):
    enabled: bool
    ttl_seconds: int = 3600
    exclude_paths: list[str] = []


@api_router.get("")
def get_lscache(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("lscache.get", identity, domain=domain)


@api_router.put("")
def set_lscache(username: str, domain: str, body: SetLscacheBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("lscache.set", identity, domain=domain, **body.model_dump())


@api_router.post("/purge")
def purge_lscache(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("lscache.purge", identity, domain=domain)


@api_router.get("/stats")
def get_lscache_stats(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("lscache.stats", identity, domain=domain)


# --- server-rendered UI -------------------------------------------------


def _exclude_paths_to_text(paths: list[str]) -> str:
    return "\n".join(paths or [])


def _parse_exclude_paths_text(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


@ui_router.get("")
def ui_lscache_home(request: Request, username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    settings_result = call_daemon("lscache.get", identity, domain=domain)
    stats = call_daemon("lscache.stats", identity, domain=domain)
    return templates.TemplateResponse(
        request,
        "lscache.html",
        {
            "identity": identity, "username": username, "domain": domain,
            "settings": settings_result, "stats": stats,
            "exclude_paths_text": _exclude_paths_to_text(settings_result["exclude_paths"]),
        },
    )


@ui_router.post("")
def ui_set_lscache(
    username: str,
    domain: str,
    enabled: str = Form(""),
    ttl_seconds: int = Form(3600),
    exclude_paths_text: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    call_daemon(
        "lscache.set", identity, domain=domain, enabled=(enabled == "1"),
        ttl_seconds=ttl_seconds, exclude_paths=_parse_exclude_paths_text(exclude_paths_text),
    )
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/lscache", status_code=303)


@ui_router.post("/purge")
def ui_purge_lscache(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    call_daemon("lscache.purge", identity, domain=domain)
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/lscache", status_code=303)
