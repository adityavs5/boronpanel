from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from starlette.requests import Request

from shared.db import read_session
from shared.models import MailDomain

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_domain_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/mail", tags=["mail"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/mail", tags=["ui:mail"])


class CreateMailDomainBody(BaseModel):
    domain: str


class CreateMailboxBody(BaseModel):
    domain: str
    local_part: str
    password: str
    quota_mb: int = 1024


@api_router.post("/domains")
def create_mail_domain(username: str, body: CreateMailDomainBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon(
        "mail.create_domain", identity, username=username, **body.model_dump()
    )


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


@ui_router.get("/{domain}")
def ui_mailboxes(request: Request, username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    with read_session() as db:
        mail_enabled = db.scalar(select(MailDomain).where(MailDomain.domain == domain)) is not None

    mailboxes = call_daemon("mail.list_mailboxes", identity, domain=domain)["mailboxes"]
    return templates.TemplateResponse(
        request,
        "mail_domain.html",
        {"identity": identity, "username": username, "domain": domain, "mailboxes": mailboxes, "mail_enabled": mail_enabled},
    )


@ui_router.post("/{domain}/enable")
def ui_enable_mail_domain(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
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
    call_daemon(
        "mail.create_mailbox", identity, domain=domain, local_part=local_part, password=password
    )
    return RedirectResponse(f"/ui/accounts/{username}/mail/{domain}", status_code=303)
