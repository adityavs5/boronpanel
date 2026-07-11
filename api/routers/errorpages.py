"""Missing-features batch, goal feature 4: custom error pages.
CRUD /accounts/{u}/domains/{d}/error-pages (the goal's own literal shape) --
GET lists all 4 codes + which have a custom page, GET /{code} previews one
(customer's own or the Boron-branded default), PUT /{code} sets the
customer's own HTML, DELETE /{code} reverts to the default."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_domain_access

api_router = APIRouter(prefix="/api/v1/accounts/{username}/domains/{domain}/error-pages", tags=["error-pages"])


class ErrorPageBody(BaseModel):
    content: str


@api_router.get("")
def list_error_pages(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("errorpages.list", identity, domain=domain)


@api_router.get("/{code}")
def get_error_page(username: str, domain: str, code: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("errorpages.get", identity, domain=domain, code=code)


@api_router.put("/{code}")
def set_error_page(username: str, domain: str, code: int, body: ErrorPageBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("errorpages.set", identity, domain=domain, code=code, content=body.content)


@api_router.delete("/{code}")
def delete_error_page(username: str, domain: str, code: int, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("errorpages.delete", identity, domain=domain, code=code)
