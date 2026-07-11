"""Missing-features batch, goal feature 5: per-mailbox spam filters.
CRUD /accounts/{u}/email/{mailbox}/spam-filters (the goal's own literal
shape) -- {mailbox} is the local-part; `domain` travels as a query param on
GET and in the request body on POST/DELETE, the same split
account_api_router's existing password-change route already uses for the
identical "mailbox lives under a domain" shape (api/routers/mail.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_account_access, require_domain_access

api_router = APIRouter(prefix="/api/v1/accounts/{username}/email/{mailbox}/spam-filters", tags=["spam-filters"])


class AddEntryBody(BaseModel):
    domain: str
    kind: str  # blacklist | whitelist
    pattern: str


class DeleteEntryBody(BaseModel):
    id: int
    domain: str


class ImportEntriesBody(BaseModel):
    domain: str
    kind: str
    text: str


@api_router.get("")
def list_entries(username: str, mailbox: str, domain: str, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, domain)
    return call_daemon("spamfilter.entries.list", identity, domain=domain, local_part=mailbox)


@api_router.post("")
def add_entry(username: str, mailbox: str, body: AddEntryBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, body.domain)
    return call_daemon("spamfilter.entries.add", identity, domain=body.domain, local_part=mailbox, kind=body.kind, pattern=body.pattern)


@api_router.delete("")
def delete_entry(username: str, mailbox: str, body: DeleteEntryBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, body.domain)
    # Cross-account IDOR guard: id alone isn't enough to authorize a
    # delete (a small sequential int, not a capability) -- the daemon
    # cross-checks the row's own domain against `domain` below, which is
    # what's actually authorized above.
    return call_daemon("spamfilter.entries.delete", identity, id=body.id, domain=body.domain)


@api_router.post("/import")
def import_entries(username: str, mailbox: str, body: ImportEntriesBody, identity: Identity = Depends(get_identity)):
    require_account_access(identity, username)
    require_domain_access(identity, body.domain)
    return call_daemon(
        "spamfilter.entries.import", identity, domain=body.domain, local_part=mailbox, kind=body.kind, text=body.text
    )
