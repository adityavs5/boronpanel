"""Phase 8 feature 2: admin account editor HTTP surface.

  PATCH /api/v1/admin/accounts/{u}/identity   -> username rename OR primary
                                                  domain OR contact email
  PATCH /api/v1/admin/accounts/{u}/passwords  -> reset the account/panel/
                                                  mailbox/database/FTP password

All admin-only. Each password `kind` dispatches to the existing, already-tested
per-resource change-password op rather than reimplementing credential logic.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.rpc import call_daemon
from api.security import Identity, get_identity, require_admin

api_router = APIRouter(prefix="/api/v1/admin/accounts", tags=["admin-identity"])


class IdentityBody(BaseModel):
    # Exactly one action per call. Rename is deliberately its own field
    # (it changes the {username} the URL keys on) rather than mixed with the
    # other edits.
    new_username: str | None = None
    primary_domain: str | None = None
    contact_email: str | None = None


@api_router.patch("/{username}/identity")
def edit_identity(username: str, body: IdentityBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    # contact_email may be sent as "" to clear, so check presence via the set
    # fields rather than truthiness.
    provided = body.model_dump(exclude_unset=True)
    if body.new_username is not None:
        return call_daemon("account.rename", identity, username=username, new_username=body.new_username)
    if body.primary_domain is not None:
        return call_daemon("account.set_primary_domain", identity, username=username, domain=body.primary_domain)
    if "contact_email" in provided:
        return call_daemon("account.set_contact_email", identity, username=username, contact_email=body.contact_email or "")
    raise HTTPException(status_code=400, detail="no identity field provided")


# kind -> (op, extra required fields beyond `password`). `account`/`panel`
# target the account itself; the others target a named sub-resource.
_PASSWORD_KINDS = {
    "account": ("account.set_password", ()),
    "panel": ("panel_user.set_password", ()),
    "mailbox": ("mail.change_password", ("domain", "local_part")),
    "database": ("db.change_password", ("name",)),
    "ftp": ("ftp.change_password", ("label",)),
}


class PasswordBody(BaseModel):
    kind: str
    password: str
    # Sub-resource identifiers, required per `kind` (see _PASSWORD_KINDS).
    domain: str | None = None
    local_part: str | None = None
    name: str | None = None
    label: str | None = None
    # For kind="panel": the customer's panel login username (defaults to the
    # account username if the caller omits it).
    panel_username: str | None = None


@api_router.patch("/{username}/passwords")
def reset_password(username: str, body: PasswordBody, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    spec = _PASSWORD_KINDS.get(body.kind)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"unknown password kind '{body.kind}'")
    op, required = spec
    params = {"password": body.password}
    if body.kind == "panel":
        params["username"] = body.panel_username or username
    else:
        params["username"] = username
        for field in required:
            value = getattr(body, field)
            if value is None:
                raise HTTPException(status_code=400, detail=f"'{field}' is required for kind '{body.kind}'")
            params[field] = value
    return call_daemon(op, identity, **params)
