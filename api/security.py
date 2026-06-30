"""Auth/RBAC for forgehost-api.

ARCHITECTURE.md SS9: two roles (admin/customer), the same auth dependency
applied uniformly across every router regardless of HTTP verb -- a direct,
deliberate countermeasure to CyberPanel's CVE-2024-51567 (their input
sanitizer only checked POST, so PUT bypassed it). Session cookies are
itsdangerous-signed (httpOnly), backed by a real Session row so they can be
revoked server-side; API tokens are SHA-256-hashed bearer tokens for
machine-to-machine use (the "integration surface for a billing system").
"""
from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass

from fastapi import Cookie, Header, HTTPException
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select

from shared.config import settings
from shared.db import read_session
from shared.models import Account, ApiToken, Domain, PanelUser, Session

COOKIE_NAME = "fh_session"
COOKIE_MAX_AGE_SECONDS = 7 * 24 * 3600

_serializer = URLSafeTimedSerializer(settings.session_secret, salt="forgehost-session")


def sign_session_id(session_id: str) -> str:
    return _serializer.dumps(session_id)


def unsign_session_id(cookie_value: str) -> str | None:
    try:
        return _serializer.loads(cookie_value, max_age=COOKIE_MAX_AGE_SECONDS)
    except BadSignature:
        return None


@dataclass
class Identity:
    panel_user_id: int
    username: str
    role: str  # admin | customer
    account_id: int | None
    auth_method: str  # session | token


def _identity_from_session_cookie(cookie_value: str) -> Identity | None:
    session_id = unsign_session_id(cookie_value)
    if session_id is None:
        return None
    with read_session() as db:
        row = db.scalar(select(Session).where(Session.session_id == session_id))
        if row is None or row.revoked:
            return None
        if row.expires_at.replace(tzinfo=dt.timezone.utc) < dt.datetime.now(dt.timezone.utc):
            return None
        user = db.get(PanelUser, row.panel_user_id)
        if user is None or user.disabled:
            return None
        return Identity(user.id, user.username, user.role, user.account_id, "session")


def _identity_from_bearer_token(token: str) -> Identity | None:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with read_session() as db:
        row = db.scalar(select(ApiToken).where(ApiToken.token_hash == token_hash))
        if row is None or row.revoked_at is not None:
            return None
        return Identity(panel_user_id=-1, username=row.label, role=row.role, account_id=row.account_id, auth_method="token")


def get_identity(
    authorization: str | None = Header(default=None),
    fh_session: str | None = Cookie(default=None),
) -> Identity:
    if authorization and authorization.lower().startswith("bearer "):
        identity = _identity_from_bearer_token(authorization[7:].strip())
        if identity is not None:
            return identity
        raise HTTPException(status_code=401, detail="invalid or revoked API token")

    if fh_session:
        identity = _identity_from_session_cookie(fh_session)
        if identity is not None:
            return identity

    raise HTTPException(status_code=401, detail="authentication required")


def require_admin(identity: Identity) -> None:
    if identity.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")


def require_account_access(identity: Identity, username: str) -> None:
    """Admins can touch any account. Customers can only touch the single
    account their panel login is scoped to -- resolved by username, not by
    trusting an account_id the client could otherwise supply directly."""
    if identity.role == "admin":
        return
    if identity.account_id is None:
        raise HTTPException(status_code=403, detail="not scoped to any account")
    with read_session() as db:
        account = db.get(Account, identity.account_id)
        if account is None or account.username != username:
            raise HTTPException(status_code=403, detail="not authorized for this account")


def require_domain_access(identity: Identity, domain: str) -> None:
    """Same as require_account_access, but for endpoints keyed by domain
    name (DNS/mail/SSL) rather than account username -- a customer must own
    the domain's account, not just any account."""
    if identity.role == "admin":
        return
    if identity.account_id is None:
        raise HTTPException(status_code=403, detail="not scoped to any account")
    with read_session() as db:
        domain_row = db.scalar(select(Domain).where(Domain.domain == domain))
        if domain_row is None or domain_row.account_id != identity.account_id:
            raise HTTPException(status_code=403, detail="not authorized for this domain")
