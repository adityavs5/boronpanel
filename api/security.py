"""HTTP identity resolution and presentation guards for boron-api.

ARCHITECTURE.md SS9: role-aware access control, with the same auth dependency
applied uniformly across every router regardless of HTTP verb -- a direct,
deliberate countermeasure to CyberPanel's CVE-2024-51567 (their input
sanitizer only checked POST, so PUT bypassed it). Session cookies are
itsdangerous-signed (httpOnly); sessions and API tokens are SHA-256-hashed in
the API-readable database. Root RPC authority is independently derived by
daemon.rpc_authority from the raw credential on each call.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field

from fastapi import Cookie, Header, HTTPException
from starlette.requests import Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy import select

from shared.config import settings
from shared.db import read_session
from shared.models import Account, ApiToken, Domain, ImpersonationSession, PanelUser, ResellerAccount, ResellerProfile, Session
from shared.validation import ValidationError, validate_domain
from shared.session_ids import session_digest

COOKIE_NAME = "fh_session"
COOKIE_MAX_AGE_SECONDS = 7 * 24 * 3600
API_TOKEN_MAX_AGE_SECONDS = 90 * 24 * 3600

_serializer = URLSafeTimedSerializer(settings.session_secret, salt="boron-session")

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
    role: str  # admin | reseller | customer
    account_id: int | None
    auth_method: str  # session | token
    # Raw proof is kept in this request-local object only and sent in the
    # top-level RPC envelope. Metadata fields are diagnostic, never proof.
    rpc_credential: str | None = field(default=None, repr=False)
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
        digest = session_digest(session_id)
        row = db.scalar(select(Session).where(Session.session_id == digest))
        if row is None or row.revoked:
            return None
        if row.expires_at.replace(tzinfo=dt.timezone.utc) < dt.datetime.now(dt.timezone.utc):
            return None
        user = db.get(PanelUser, row.panel_user_id)
        if user is None or user.disabled or user.role not in ("admin", "reseller", "customer"):
            return None
        # Phase 8 feature 1: if this session is an active impersonation, the
        # underlying PanelUser is the ADMIN, but the identity must be
        # downscoped to a customer for the impersonated account -- so it can
        # only touch that one account and can never reach an admin endpoint.
        imp = db.scalar(
            select(ImpersonationSession).where(
                ImpersonationSession.session_id == digest,
                ImpersonationSession.ended_at.is_(None),
            )
        )
        if imp is not None:
            account = db.get(Account, imp.account_id)
            if account is None or user.role != "admin" or imp.admin_panel_user_id != user.id:
                return None
            return Identity(
                panel_user_id=user.id, username=imp.admin_username, role="customer",
                account_id=imp.account_id, auth_method="session", rpc_credential=session_id,
                impersonator=imp.admin_username, impersonated_account=account.username,
            )
        if user.role == "customer":
            account = db.get(Account, user.account_id) if user.account_id is not None else None
            if account is None or account.status != "active":
                return None
        if user.role == "reseller" and db.scalar(select(ResellerProfile.id).where(
            ResellerProfile.panel_user_id == user.id, ResellerProfile.status == "active",
        )) is None:
            return None
        return Identity(user.id, user.username, user.role, user.account_id, "session", rpc_credential=session_id)


def _identity_from_bearer_token(token: str) -> Identity | None:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with read_session() as db:
        row = db.scalar(select(ApiToken).where(ApiToken.token_hash == token_hash))
        if row is None or row.revoked_at is not None:
            return None
        created = row.created_at.replace(tzinfo=dt.timezone.utc) if row.created_at.tzinfo is None else row.created_at
        if (dt.datetime.now(dt.timezone.utc) - created).total_seconds() > API_TOKEN_MAX_AGE_SECONDS:
            return None
        if row.role not in ("admin", "customer"):
            return None
        if row.role == "customer":
            account = db.get(Account, row.account_id) if row.account_id is not None else None
            if account is None or account.status != "active":
                return None
        return Identity(panel_user_id=-1, username=row.label, role=row.role, account_id=row.account_id,
                        auth_method="token", rpc_credential=token)


def enforce_listener_role(identity,connection):
    from shared.panel_ports import listener_ports
    admin,customer=listener_ports()
    if admin==customer:return
    server=connection.scope.get('server')
    port=server[1] if server else None
    role='admin' if identity.impersonator else identity.role
    expected=admin if role in ('admin', 'reseller') else customer
    if port!=expected:
        raise HTTPException(status_code=403,detail=f'Use the {role} panel on port {expected}.')


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
            enforce_listener_role(identity,request)
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
            enforce_listener_role(identity,request)
            identity.ip = client_ip
            request.state.identity = identity
            return identity

    raise HTTPException(status_code=401, detail="authentication required")


def require_admin(identity: Identity) -> None:
    if identity.role != "admin":
        raise HTTPException(status_code=403, detail="admin role required")


def require_reseller(identity: Identity) -> None:
    if identity.role != "reseller":
        raise HTTPException(status_code=403, detail="reseller role required")


def _reseller_owns_account(db, identity: Identity, account_id: int) -> bool:
    profile_id = db.scalar(select(ResellerProfile.id).where(
        ResellerProfile.panel_user_id == identity.panel_user_id,
        ResellerProfile.status == "active",
    ))
    if profile_id is None:
        return False
    return db.scalar(select(ResellerAccount.id).where(
        ResellerAccount.reseller_id == profile_id,
        ResellerAccount.account_id == account_id,
    )) is not None


def require_account_access(identity: Identity, username: str) -> None:
    """Admins can touch any account. Customers can only touch the single
    account their panel login is scoped to -- resolved by username, not by
    trusting an account_id the client could otherwise supply directly."""
    if identity.role == "admin":
        return
    if identity.role == "reseller":
        with read_session() as db:
            account_id = db.scalar(select(Account.id).where(Account.username == username))
            if account_id is not None and _reseller_owns_account(db, identity, account_id):
                return
        raise HTTPException(status_code=403, detail="not authorized for this account")
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
    if identity.role == "reseller":
        try:
            normalized_domain = validate_domain(domain)
        except ValidationError as exc:
            raise HTTPException(status_code=403, detail="not authorized for this domain") from exc
        with read_session() as db:
            account_id = db.scalar(select(Domain.account_id).where(Domain.domain == normalized_domain))
            if account_id is not None and _reseller_owns_account(db, identity, account_id):
                return
        raise HTTPException(status_code=403, detail="not authorized for this domain")
    if identity.account_id is None:
        raise HTTPException(status_code=403, detail="not scoped to any account")
    try:
        # Domain rows are stored in the canonical form produced by the daemon
        # (lowercase, stripped, and without a trailing dot). Normalize the
        # route value before looking it up so a legitimate owner is not denied
        # merely because the browser supplied a different DNS spelling.
        normalized_domain = validate_domain(domain)
    except ValidationError as exc:
        raise HTTPException(status_code=403, detail="not authorized for this domain") from exc
    with read_session() as db:
        domain_row = db.scalar(
            select(Domain).where(
                Domain.domain == normalized_domain,
                Domain.account_id == identity.account_id,
            )
        )
        if domain_row is None:
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
