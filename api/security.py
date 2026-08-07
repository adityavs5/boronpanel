"""Auth/RBAC for boron-api.

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
from starlette.requests import Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select

from shared.config import settings
from shared.db import read_session
from shared.models import Account, ApiToken, Domain, ImpersonationSession, PanelUser, Session

COOKIE_NAME = "fh_session"
COOKIE_MAX_AGE_SECONDS = 7 * 24 * 3600
API_TOKEN_MAX_AGE_SECONDS = 90 * 24 * 3600

_serializer = URLSafeTimedSerializer(settings.session_secret, salt="boron-session")

# Phase 5 feature 10: TOTP 2FA login's second step. A separate salt from
# the real session serializer above -- this token only ever proves
# "the password check already passed for this panel_user_id a moment
# ago," never grants an authenticated session by itself, so it's
# deliberately short-lived (5 minutes) and carried as a hidden form
# field, not a cookie.
_TWOFACTOR_PENDING_MAX_AGE_SECONDS = 5 * 60
_twofactor_serializer = URLSafeTimedSerializer(settings.session_secret, salt="boron-2fa-pending")


def sign_twofactor_pending(panel_user_id: int) -> str:
    return _twofactor_serializer.dumps(panel_user_id)


def unsign_twofactor_pending(token: str) -> int | None:
    try:
        return _twofactor_serializer.loads(token, max_age=_TWOFACTOR_PENDING_MAX_AGE_SECONDS)
    except BadSignature:
        return None


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
    # Phase 8 feature 1: set only while an admin is impersonating a customer.
    # `impersonator` is the admin's panel username (also carried as this
    # Identity's audit `username`, so actions taken while impersonating are
    # attributable to the admin); `impersonated_account` is the account being
    # acted as. When set, `role` is forced to "customer" and `account_id` to
    # the impersonated account, so an impersonation session can never reach an
    # admin-only endpoint even though its underlying session belongs to an
    # admin panel user.
    impersonator: str | None = None
    impersonated_account: str | None = None
    # Client IP of the request this identity was resolved for — stamped by
    # get_identity, forwarded to the daemon (`_ip`) so the account-events log
    # can record where lifecycle actions came from.
    ip: str | None = None

    @property
    def is_impersonating(self) -> bool:
        return self.impersonator is not None


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
        # Phase 8 feature 1: if this session is an active impersonation, the
        # underlying PanelUser is the ADMIN, but the identity must be
        # downscoped to a customer for the impersonated account -- so it can
        # only touch that one account and can never reach an admin endpoint.
        imp = db.scalar(
            select(ImpersonationSession).where(
                ImpersonationSession.session_id == session_id,
                ImpersonationSession.ended_at.is_(None),
            )
        )
        if imp is not None:
            account = db.get(Account, imp.account_id)
            if account is None:
                return None
            return Identity(
                panel_user_id=user.id,
                username=imp.admin_username,
                role="customer",
                account_id=imp.account_id,
                auth_method="session",
                impersonator=imp.admin_username,
                impersonated_account=account.username,
            )
        return Identity(user.id, user.username, user.role, user.account_id, "session")


def _identity_from_bearer_token(token: str) -> Identity | None:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with read_session() as db:
        row = db.scalar(select(ApiToken).where(ApiToken.token_hash == token_hash))
        if row is None or row.revoked_at is not None:
            return None
        created = row.created_at.replace(tzinfo=dt.timezone.utc) if row.created_at.tzinfo is None else row.created_at
        if (dt.datetime.now(dt.timezone.utc) - created).total_seconds() > API_TOKEN_MAX_AGE_SECONDS:
            return None
        return Identity(panel_user_id=-1, username=row.label, role=row.role, account_id=row.account_id, auth_method="token")


def get_identity(
    request: Request,
    authorization: str | None = Header(default=None),
    fh_session: str | None = Cookie(default=None),
) -> Identity:
    # uvicorn serves clients directly (no trusted reverse proxy), so the
    # socket peer address IS the client address; X-Forwarded-For would be
    # client-spoofable here and is deliberately ignored.
    client_ip = request.client.host if request.client else None

    if authorization and authorization.lower().startswith("bearer "):
        identity = _identity_from_bearer_token(authorization[7:].strip())
        if identity is not None:
            identity.ip = client_ip
            # Run A feature 7: let the access-log middleware name the user
            # without repeating this resolution (it runs outside every
            # dependency, so request.state is populated by the time it reads).
            request.state.identity = identity
            return identity
        raise HTTPException(status_code=401, detail="invalid or revoked API token")

    if fh_session:
        identity = _identity_from_session_cookie(fh_session)
        if identity is not None:
            identity.ip = client_ip
            request.state.identity = identity
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


def require_customer_self_access(identity: Identity, username: str) -> None:
    """Phase 4 feature 6's own explicit scoping: "Customer panel only --
    admin cannot see account SSH keys." A deliberate exception to every
    other resource in this project, where an admin can always act on any
    account -- an SSH key grants real interactive shell access to that
    account's files (daemon/sysops.py's LOGIN_SHELL), and the goal singles
    this one resource out as customer-eyes-only, so admin is rejected
    here even though require_account_access would normally let it through.
    """
    if identity.role != "customer" or identity.account_id is None:
        raise HTTPException(status_code=403, detail="SSH key management is customer-only")
    with read_session() as db:
        account = db.get(Account, identity.account_id)
        if account is None or account.username != username:
            raise HTTPException(status_code=403, detail="not authorized for this account")
