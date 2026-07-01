from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access
from api.templates import templates

api_router = APIRouter(prefix="/api/v1/accounts/{username}/domains/{domain}/email", tags=["email"])
ui_router = APIRouter(prefix="/ui/accounts/{username}/domains/{domain}/email", tags=["ui:email"])


class ForwarderBody(BaseModel):
    local_part: str
    destination: str


class CatchallBody(BaseModel):
    destination: str


class AutoresponderBody(BaseModel):
    local_part: str
    subject: str
    body: str
    start_date: str | None = None
    end_date: str | None = None


# --- Forwarders --------------------------------------------------------


@api_router.get("/forwarders")
def list_forwarders(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("mail.forward.list", identity, domain=domain)


@api_router.post("/forwarders")
def create_forwarder(username: str, domain: str, body: ForwarderBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("mail.forward.create", identity, domain=domain, **body.model_dump())


@api_router.delete("/forwarders")
def delete_forwarder(username: str, domain: str, local_part: str, destination: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("mail.forward.delete", identity, domain=domain, local_part=local_part, destination=destination)


# --- Catch-all -----------------------------------------------------------


@api_router.get("/catchall")
def get_catchall(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("mail.catchall.get", identity, domain=domain)


@api_router.post("/catchall")
def set_catchall(username: str, domain: str, body: CatchallBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("mail.catchall.set", identity, domain=domain, **body.model_dump())


@api_router.delete("/catchall")
def delete_catchall(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("mail.catchall.delete", identity, domain=domain)


# --- Autoresponders ------------------------------------------------------


@api_router.get("/autoresponders/{local_part}")
def get_autoresponder(username: str, domain: str, local_part: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("mail.autoresponder.get", identity, domain=domain, local_part=local_part)


@api_router.post("/autoresponders")
def set_autoresponder(username: str, domain: str, body: AutoresponderBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("mail.autoresponder.set", identity, domain=domain, **body.model_dump())


@api_router.delete("/autoresponders/{local_part}")
def delete_autoresponder(username: str, domain: str, local_part: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    return call_daemon("mail.autoresponder.delete", identity, domain=domain, local_part=local_part)


# --- UI -------------------------------------------------------------------


@ui_router.get("")
def ui_email_home(request: Request, username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    forwards = call_daemon("mail.forward.list", identity, domain=domain)["forwards"]
    catchall = call_daemon("mail.catchall.get", identity, domain=domain)["catchall"]
    mailboxes = call_daemon("mail.list_mailboxes", identity, domain=domain)["mailboxes"]

    autoresponders = {}
    for mbox in mailboxes:
        current = call_daemon("mail.autoresponder.get", identity, domain=domain, local_part=mbox["local_part"])["autoresponder"]
        if current:
            autoresponders[mbox["local_part"]] = current

    return templates.TemplateResponse(
        request,
        "email_features.html",
        {
            "identity": identity,
            "username": username,
            "domain": domain,
            "forwards": forwards,
            "catchall": catchall,
            "mailboxes": mailboxes,
            "autoresponders": autoresponders,
        },
    )


@ui_router.post("/forwarders")
def ui_create_forwarder(
    username: str,
    domain: str,
    local_part: str = Form(...),
    destination: str = Form(...),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    call_daemon("mail.forward.create", identity, domain=domain, local_part=local_part, destination=destination)
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/email", status_code=303)


@ui_router.post("/forwarders/delete")
def ui_delete_forwarder(
    username: str,
    domain: str,
    local_part: str = Form(...),
    destination: str = Form(...),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    call_daemon("mail.forward.delete", identity, domain=domain, local_part=local_part, destination=destination)
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/email", status_code=303)


@ui_router.post("/catchall")
def ui_set_catchall(username: str, domain: str, destination: str = Form(...), identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("mail.catchall.set", identity, domain=domain, destination=destination)
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/email", status_code=303)


@ui_router.post("/catchall/delete")
def ui_delete_catchall(username: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("mail.catchall.delete", identity, domain=domain)
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/email", status_code=303)


@ui_router.post("/autoresponders")
def ui_set_autoresponder(
    username: str,
    domain: str,
    local_part: str = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
    start_date: str = Form(""),
    end_date: str = Form(""),
    identity: Identity = Depends(get_identity),
):
    require_account_access(identity, username)
    call_daemon(
        "mail.autoresponder.set",
        identity,
        domain=domain,
        local_part=local_part,
        subject=subject,
        body=body,
        start_date=start_date or None,
        end_date=end_date or None,
    )
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/email", status_code=303)


@ui_router.post("/autoresponders/{local_part}/delete")
def ui_delete_autoresponder(username: str, domain: str, local_part: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    call_daemon("mail.autoresponder.delete", identity, domain=domain, local_part=local_part)
    return RedirectResponse(f"/ui/accounts/{username}/domains/{domain}/email", status_code=303)
