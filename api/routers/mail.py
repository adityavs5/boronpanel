from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.requests import Request

from shared.config import settings
from shared.db import read_session
from shared.models import Account, Domain, MailDomain, MailUser

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


class MailboxQuotaBody(BaseModel):
    quota_mb: int


@api_router.patch('/domains/{domain}/mailboxes/{local_part}/quota')
def set_mailbox_quota(domain: str, local_part: str, body: MailboxQuotaBody, identity: Identity = Depends(get_identity)):
    require_domain_access(identity, domain)
    return call_daemon('mail.set_quota', identity, domain=domain, local_part=local_part, quota_mb=body.quota_mb)


class ChangeMailboxPasswordBody(BaseModel):
    domain: str
    password: str


class MailboxActiveBody(BaseModel):
    active: bool


class WebmailSessionBody(BaseModel):
    domain: str


class MailDnsRepairBody(BaseModel):
    replace_conflicts: list[str] = Field(default_factory=list)


class DmarcBody(BaseModel):
    policy: str = "none"
    rua: str | None = None
    subdomain_policy: str | None = None


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


@api_router.patch("/domains/{domain}/mailboxes/{local_part}")
def set_mailbox_active(domain: str, local_part: str, body: MailboxActiveBody, identity: Identity = Depends(get_identity)):
    require_domain_access(identity, domain)
    return call_daemon(
        "mail.set_mailbox_active", identity,
        domain=domain, local_part=local_part, active=body.active,
    )


@account_api_router.patch("/{local_part}/password")
def change_mailbox_password(username: str, local_part: str, body: ChangeMailboxPasswordBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, body.domain)
    return call_daemon("mail.change_password", identity, domain=body.domain, local_part=local_part, password=body.password)


@account_api_router.post("/{local_part}/webmail-session")
def create_webmail_session(
    username: str,
    local_part: str,
    body: WebmailSessionBody,
    request: Request,
    response: Response,
    identity: Identity = Depends(get_identity),
):
    """Create a one-use Roundcube handoff for an interactive authorized session."""
    require_account_access(identity, username)
    require_domain_access(identity, body.domain)
    invalid_impersonation = identity.is_impersonating and (
        identity.role != "customer"
        or identity.account_id is None
        or identity.impersonated_account != username
    )
    if (identity.auth_method != "session" or invalid_impersonation
            or identity.role not in ("customer", "admin")):
        raise HTTPException(status_code=403, detail="An authorized customer or administrator session is required to open webmail")
    expected = f"{request.url.scheme}://{request.url.netloc}"
    if request.headers.get("origin") != expected:
        raise HTTPException(status_code=403, detail="same-origin request required")
    result = call_daemon(
        "webmail.launch.create", identity, username=username,
        mailbox=f"{local_part}@{body.domain}",
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return result


@account_api_router.get("/mailboxes")
def list_account_mailboxes(username: str, identity: Identity = Depends(get_identity)):
    """Return the owned mailbox inventory used by guided backup selectors.

    This read stays in the unprivileged API process.  Ownership is derived
    from the account -> mail-domain relationship; callers cannot supply a
    domain to widen the result set.
    """
    require_account_access(identity, username)
    with read_session() as db:
        account = db.scalar(select(Account).where(Account.username == username))
        if account is None:
            return {"domains": [], "mailboxes": []}
        domains = db.scalars(
            select(MailDomain).where(MailDomain.account_id == account.id).order_by(MailDomain.domain)
        ).all()
        domain_names = [row.domain for row in domains]
        if not domain_names:
            return {"domains": [], "mailboxes": []}
        rows = db.scalars(
            select(MailUser)
            .where(MailUser.domain.in_(domain_names))
            .order_by(MailUser.domain, MailUser.local_part)
        ).all()
        return {
            "domains": domain_names,
            "mailboxes": [
                {
                    "domain": row.domain,
                    "local_part": row.local_part,
                    "address": f"{row.local_part}@{row.domain}",
                    "active": True,
                }
                for row in rows
            ],
        }


@account_api_router.get("/domains/{domain}/dns-readiness")
def mail_dns_readiness(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("mail.dns.preview", identity, username=username, domain=domain)


@account_api_router.post("/domains/{domain}/dns-repair")
def repair_mail_dns(username: str, domain: str, body: MailDnsRepairBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon(
        "mail.dns.repair", identity, username=username, domain=domain,
        replace_conflicts=body.replace_conflicts,
    )


@account_api_router.put("/domains/{domain}/dmarc")
def set_dmarc(username: str, domain: str, body: DmarcBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("mail.dns.dmarc", identity, username=username, domain=domain, **body.model_dump())


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
