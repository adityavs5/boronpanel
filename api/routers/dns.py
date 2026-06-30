from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from starlette.requests import Request

from shared.db import read_session
from shared.models import Account, Domain

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_admin, require_domain_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/dns", tags=["dns"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/dns", tags=["ui:dns"])


class CreateZoneBody(BaseModel):
    domain: str


class SetRecordBody(BaseModel):
    domain: str
    subdomain: str = "@"
    type: str
    values: list[str]
    ttl: int = 3600


@api_router.post("/zones")
def create_zone(body: CreateZoneBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    # The zone's owning account comes from the *web* domain's existing
    # Domain row (set by domain.add), never from the admin's own panel
    # username -- passing identity.username here was a real bug caught by
    # live testing: an admin logged in as "admin" creating a zone failed
    # with "account 'admin' not found", because "admin" isn't a hosting
    # account. require_domain_access (used by every other DNS endpoint)
    # already authorizes off this same Domain row, not DnsZone.account_id,
    # so DnsZone.account_id is purely a display/record-keeping field here,
    # not a security boundary -- but it should still be correct.
    with read_session() as db:
        domain_row = db.scalar(select(Domain).where(Domain.domain == body.domain))
        owner_username = None
        if domain_row is not None:
            account = db.get(Account, domain_row.account_id)
            owner_username = account.username if account else None

    params = body.model_dump()
    if owner_username:
        params["username"] = owner_username
    return call_daemon("dns.create_zone", identity, **params)


@api_router.get("/zones/{domain}/records")
def list_records(domain: str, identity: Identity = Depends(get_identity)):
    require_domain_access(identity, domain)
    return call_daemon("dns.list_records", identity, domain=domain)


@api_router.put("/zones/{domain}/records")
def set_record(domain: str, body: SetRecordBody, identity: Identity = Depends(get_identity)):
    require_domain_access(identity, domain)
    return call_daemon("dns.set_record", identity, **body.model_dump())


@api_router.delete("/zones/{domain}/records")
def delete_record(domain: str, subdomain: str, type: str, identity: Identity = Depends(get_identity)):
    require_domain_access(identity, domain)
    return call_daemon(
        "dns.delete_record", identity, domain=domain, subdomain=subdomain, type=type
    )


@ui_router.get("/{domain}")
def ui_zone_records(request: Request, username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    records = call_daemon("dns.list_records", identity, domain=domain)["records"]
    return templates.TemplateResponse(
        request, "dns_zone.html", {"identity": identity, "username": username, "domain": domain, "records": records}
    )


@ui_router.post("/{domain}")
def ui_set_record(
    username: str,
    domain: str,
    subdomain: str = Form("@"),
    type: str = Form(...),
    value: str = Form(...),
    ttl: int = Form(3600),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    call_daemon(
        "dns.set_record",
        identity,
        domain=domain,
        subdomain=subdomain,
        type=type,
        values=[value],
        ttl=ttl,
    )
    return RedirectResponse(f"/ui/accounts/{username}/dns/{domain}", status_code=303)
