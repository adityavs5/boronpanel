"""Phase 8 feature 1: "Login as user" (admin impersonation).

An admin mints a single-use, 5-minute token for an account
(`impersonation.create_token`); redeeming it (`impersonation.redeem_token`)
opens a customer-scoped session for that account, recorded in an
ImpersonationSession row that api/security.get_identity uses to DOWNSCOPE the
underlying admin-owned session to a customer identity. "Return to admin"
(`impersonation.end`) revokes the impersonation session and hands back the
admin's original session id to restore.

Every op here routes through server.py's dispatch(), so issuance, redemption,
and end are all audit-logged automatically; and because the impersonation
session's actions carry the admin's username as their audit actor (see
api/security.get_identity), every action taken *while* impersonating is
attributable to the admin who did it, not just to the account.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import string

from sqlalchemy import select

from shared.db import write_session
from shared.models import (
    Account,
    ImpersonationSession,
    ImpersonationToken,
    PanelUser,
    Session,
    utcnow,
)

# 5 minutes, per the goal's explicit spec. The token is only ever a bridge
# from "admin clicked Login as user" to "customer session created a moment
# later"; a longer window buys nothing and widens the replay surface.
TOKEN_TTL_SECONDS = 5 * 60
# The impersonation session itself is deliberately short-lived -- an admin
# doing support work, not a durable customer login. 2 hours is generous for
# that while bounding how long a walked-away impersonation cookie stays live.
IMPERSONATION_SESSION_TTL_HOURS = 2


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _generate_token() -> tuple[str, str]:
    raw = "fh_imp_" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(40))
    return raw, _hash(raw)


def create_token(params: dict) -> dict:
    """Mint a single-use impersonation token for `username` (the hosting
    account). Only an admin ever reaches this (require_admin in the router);
    `admin_username` is the authenticated admin, recorded so only they can
    later redeem it."""
    username = params["username"]
    admin_username = params["admin_username"]
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        if account.status not in ("active", "suspended"):
            raise RuntimeError(
                f"cannot impersonate an account in status '{account.status}' -- only active/suspended accounts"
            )
        raw, token_hash = _generate_token()
        expires_at = utcnow() + dt.timedelta(seconds=TOKEN_TTL_SECONDS)
        session.add(
            ImpersonationToken(
                token_hash=token_hash,
                account_id=account.id,
                admin_username=admin_username,
                expires_at=expires_at,
            )
        )
        return {
            "token": raw,
            "account_username": username,
            "expires_at": expires_at.isoformat(),
            "expires_in_seconds": TOKEN_TTL_SECONDS,
        }


def redeem_token(params: dict) -> dict:
    """Exchange a valid, unused, unexpired token for a fresh customer-scoped
    session. The redeeming admin must be the same admin who issued the token
    (admin_username match) and present their current admin session id
    (admin_session_id) so "Return to admin" can restore it afterward."""
    raw = params["token"]
    admin_username = params["admin_username"]
    admin_panel_user_id = params["admin_panel_user_id"]
    admin_session_id = params.get("admin_session_id")
    token_hash = _hash(raw)
    now = utcnow()

    with write_session() as session:
        row = session.scalar(select(ImpersonationToken).where(ImpersonationToken.token_hash == token_hash))
        if row is None:
            raise RuntimeError("impersonation token is invalid")
        if row.used_at is not None:
            raise RuntimeError("impersonation token has already been used")
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=dt.timezone.utc)
        if expires_at <= now:
            raise RuntimeError("impersonation token has expired")
        if row.admin_username != admin_username:
            # Only the issuing admin can redeem -- a token leaked to another
            # admin (or captured) is useless without also being that admin.
            raise RuntimeError("impersonation token was issued to a different admin")

        account = session.get(Account, row.account_id)
        if account is None or account.status not in ("active", "suspended"):
            raise RuntimeError("target account is no longer impersonatable")

        # Mark used FIRST (single-use), inside the same transaction that
        # creates the session -- so a crash between the two can never leave a
        # consumed token that produced no session, nor a session from a token
        # still marked reusable.
        row.used_at = now

        admin_user = session.get(PanelUser, admin_panel_user_id)
        if admin_user is None or admin_user.role != "admin":
            raise RuntimeError("redeeming panel user is not an admin")

        session_id = secrets.token_urlsafe(32)
        expires = now + dt.timedelta(hours=IMPERSONATION_SESSION_TTL_HOURS)
        # panel_user_id = the ADMIN's own id: the session stays owned by, and
        # revocable through, a real panel user. get_identity downscopes it to
        # a customer identity for account_id via the ImpersonationSession row.
        session.add(Session(session_id=session_id, panel_user_id=admin_panel_user_id, expires_at=expires))
        session.add(
            ImpersonationSession(
                session_id=session_id,
                account_id=account.id,
                admin_panel_user_id=admin_panel_user_id,
                admin_username=admin_username,
                admin_session_id=admin_session_id,
            )
        )
        return {
            "session_id": session_id,
            "account_id": account.id,
            "account_username": account.username,
            "expires_at": expires.isoformat(),
        }


def end(params: dict) -> dict:
    """End the impersonation session identified by `session_id` (the current
    impersonation cookie's session id): revoke it and return the admin's
    original session id to restore. Idempotent -- ending an already-ended or
    unknown session just reports no admin session to restore."""
    session_id = params["session_id"]
    with write_session() as session:
        imp = session.scalar(
            select(ImpersonationSession).where(ImpersonationSession.session_id == session_id)
        )
        if imp is None:
            return {"admin_session_id": None, "account_username": None, "status": "not_impersonating"}
        admin_session_id = imp.admin_session_id
        account = session.get(Account, imp.account_id)
        account_username = account.username if account else None
        if imp.ended_at is None:
            imp.ended_at = utcnow()
        # Revoke the impersonation session itself so its cookie is dead the
        # instant we return, regardless of whether the admin session restores.
        sess = session.scalar(select(Session).where(Session.session_id == session_id))
        if sess is not None:
            sess.revoked = True
        return {
            "admin_session_id": admin_session_id,
            "account_username": account_username,
            "status": "ended",
        }
