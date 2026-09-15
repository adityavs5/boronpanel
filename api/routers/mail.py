from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from starlette.requests import Request

from shared.config import settings
from shared.db import read_session
from shared.models import Account, Domain, MailDomain

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_domain_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/mail", tags=["mail"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/mail", tags=["ui:mail"])

# Phase 3 feature 10: the goal's literal password-manager API shape is
# account-scoped (/accounts/{u}/email/{m}/password) -- added alongside
# the existing domain-scoped mail routes above (kept as-is) rather than
# replacing them, same pattern used for Phase 3 feature 8's SSL
# dashboard routes.
account_api_router = APIRouter(prefix="/api/v1/accounts/{username}/email", tags=["mail"])


class CreateMailDomainBody(BaseModel):
    domain: str


class CreateMailboxBody(BaseModel):
    domain: str
    local_part: str
    password: str
    quota_mb: int = 1024


class ChangeMailboxPasswordBody(BaseModel):
    domain: str
    password: str


@api_router.get("/webmail")
def webmail_settings(identity: Identity = Depends(get_identity)):
    """Expose only the public webmail URL to authenticated panel users."""
    return {"enabled": bool(settings.webmail_url), "url": settings.webmail_url}


@api_router.post("/domains")
def create_mail_domain(body: CreateMailDomainBody, identity: Identity = Depends(get_identity)):
    # This route has no {username} in its path (it's prefixed /api/v1/mail,
    # not /api/v1/accounts/{username}/mail) -- an earlier version declared
    # a bare `username: str` parameter anyway, which FastAPI silently
    # turned into a *required query parameter* instead of erroring at
    # startup, since nothing here binds it to the URL path. The first real
    # call (during final E2E validation) failed with a confusing
    # "Field required" for a "username" nobody was meant to pass.
    # Fixed the same way as dns.create_zone's analogous bug: authorize and
    # attribute ownership from the domain's existing Domain row (set by
    # domain.add), not from a parameter that was never wired to anything.
    require_domain_access(identity, body.domain)
    with read_session() as db:
        domain_row = db.scalar(select(Domain).where(Domain.domain == body.domain))
        owner_username = None
        if domain_row is not None:
            account = db.get(Account, domain_row.account_id)
            owner_username = account.username if account else None
    params = body.model_dump()
    if owner_username:
        params["username"] = owner_username
    return call_daemon("mail.create_domain", identity, **params)


@api_router.post("/mailboxes")
def create_mailbox(body: CreateMailboxBody, identity: Identity = Depends(get_identity)):
    require_domain_access(identity, body.domain)
    return call_daemon("mail.create_mailbox", identity, **body.model_dump())


@api_router.get("/domains/{domain}/mailboxes")
def list_mailboxes(domain: str, identity: Identity = Depends(get_identity)):
    require_domain_access(identity, domain)
    return call_daemon("mail.list_mailboxes", identity, domain=domain)


@api_router.delete("/domains/{domain}/mailboxes/{local_part}")
def delete_mailbox(domain: str, local_part: str, identity: Identity = Depends(get_identity)):
    require_domain_access(identity, domain)
    return call_daemon(
        "mail.delete_mailbox", identity, domain=domain, local_part=local_part
    )


@account_api_router.patch("/{local_part}/password")
def change_mailbox_password(username: str, local_part: str, body: ChangeMailboxPasswordBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, body.domain)
    return call_daemon("mail.change_password", identity, domain=body.domain, local_part=local_part, password=body.password)


@ui_router.get("/{domain}")
def ui_mailboxes(request: Request, username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    with read_session() as db:
        mail_enabled = db.scalar(select(MailDomain).where(MailDomain.domain == domain)) is not None

    mailboxes = call_daemon("mail.list_mailboxes", identity, domain=domain)["mailboxes"]
    return templates.TemplateResponse(
        request,
        "mail_domain.html",
        {
            "identity": identity,
            "username": username,
            "domain": domain,
            "mailboxes": mailboxes,
            "mail_enabled": mail_enabled,
            "webmail_url": settings.webmail_url,
        },
    )


@ui_router.post("/{domain}/enable")
def ui_enable_mail_domain(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    call_daemon("mail.create_domain", identity, username=username, domain=domain)
    return RedirectResponse(f"/ui/accounts/{username}/mail/{domain}", status_code=303)


@ui_router.post("/{domain}")
def ui_create_mailbox(
    username: str,
    domain: str,
    local_part: str = Form(...),
    password: str = Form(...),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    call_daemon(
        "mail.create_mailbox", identity, domain=domain, local_part=local_part, password=password
    )
    return RedirectResponse(f"/ui/accounts/{username}/mail/{domain}", status_code=303)


@ui_router.post("/{domain}/{local_part}/password")
def ui_change_mailbox_password(
    username: str,
    domain: str,
    local_part: str,
    password: str = Form(...),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    call_daemon("mail.change_password", identity, domain=domain, local_part=local_part, password=password)
    return RedirectResponse(f"/ui/accounts/{username}/mail/{domain}", status_code=303)
