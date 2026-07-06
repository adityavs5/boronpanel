"""Phase 8 features 5 & 6: email delivery log + per-domain email routing.

  GET   /api/v1/accounts/{u}/email/delivery-log
  GET   /api/v1/accounts/{u}/domains/{d}/email/routing
  PATCH /api/v1/accounts/{u}/domains/{d}/email/routing
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_domain_access

delivery_log_router = APIRouter(prefix="/api/v1/accounts/{username}/email", tags=["email-delivery-log"])
routing_router = APIRouter(prefix="/api/v1/accounts/{username}/domains/{domain}/email", tags=["email-routing"])


@delivery_log_router.get("/delivery-log")
def delivery_log(username: str, search: str = "", limit: int = 500, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("maillog.delivery", identity, username=username, search=search, limit=limit)


class RoutingBody(BaseModel):
    mode: str  # local | remote | backup


@routing_router.get("/routing")
def get_routing(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("email_routing.get", identity, username=username, domain=domain)


@routing_router.patch("/routing")
def set_routing(username: str, domain: str, body: RoutingBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("email_routing.set", identity, username=username, domain=domain, mode=body.mode)
