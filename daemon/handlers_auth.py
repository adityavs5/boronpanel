"""Panel auth primitives forgehostd exposes so forgehost-api (which only
ever opens SQLite read-only, ARCHITECTURE.md SS4) can still create/revoke
sessions and API tokens -- the only *writes* auth needs. Password
verification itself is plain application logic (no privileged system
action involved) and happens in forgehost-api directly against a read-only
PanelUser row; only the resulting state changes (new session row, revoked
session, new/revoked token, new panel user) route through here.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import string

from sqlalchemy import select

from shared.db import write_session
from shared.models import ApiToken, PanelUser, Session
from shared.passwords import hash_password
from shared.validation import ValidationError, validate_password_strength

SESSION_TTL_HOURS = 24 * 7
TOKEN_PREFIX_LEN = 8


def _panel_user_dict(user: PanelUser) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "account_id": user.account_id,
        "disabled": user.disabled,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def create_panel_user(params: dict) -> dict:
    username = params["username"]
    password = params["password"]
    role = params.get("role", "customer")
    account_id = params.get("account_id")

    if role not in ("admin", "customer"):
        raise ValidationError("role must be 'admin' or 'customer'")
    if role == "customer" and account_id is None:
        raise ValidationError("customer panel users must have an account_id")
    password = validate_password_strength(password)

    with write_session() as session:
        existing = session.scalar(select(PanelUser).where(PanelUser.username == username))
        if existing is not None:
            raise RuntimeError(f"panel user '{username}' already exists")
        user = PanelUser(
            username=username,
            password_hash=hash_password(password),
            role=role,
            account_id=account_id,
        )
        session.add(user)
        session.flush()
        return _panel_user_dict(user)


def set_panel_user_password(params: dict) -> dict:
    username = params["username"]
    new_password = validate_password_strength(params["password"])
    with write_session() as session:
        user = session.scalar(select(PanelUser).where(PanelUser.username == username))
        if user is None:
            raise RuntimeError(f"panel user '{username}' not found")
        user.password_hash = hash_password(new_password)
        return {"username": username, "status": "password_changed"}


def create_session(params: dict) -> dict:
    panel_user_id = params["panel_user_id"]
    session_id = secrets.token_urlsafe(32)
    expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=SESSION_TTL_HOURS)
    with write_session() as db:
        user = db.get(PanelUser, panel_user_id)
        if user is None or user.disabled:
            raise RuntimeError("panel user not found or disabled")
        row = Session(session_id=session_id, panel_user_id=panel_user_id, expires_at=expires_at)
        db.add(row)
    return {"session_id": session_id, "expires_at": expires_at.isoformat()}


def revoke_session(params: dict) -> dict:
    session_id = params["session_id"]
    with write_session() as db:
        row = db.scalar(select(Session).where(Session.session_id == session_id))
        if row is not None:
            row.revoked = True
    return {"status": "revoked"}


def _generate_token(role: str) -> tuple[str, str]:
    raw = f"fh_{role}_" + "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(40))
    token_hash = hashlib.sha256(raw.encode()).hexdigest()
    return raw, token_hash


def create_api_token(params: dict) -> dict:
    label = params["label"]
    role = params.get("role", "admin")
    account_id = params.get("account_id")
    if role not in ("admin", "customer"):
        raise ValidationError("role must be 'admin' or 'customer'")

    raw_token, token_hash = _generate_token(role)
    with write_session() as db:
        row = ApiToken(token_hash=token_hash, label=label, role=role, account_id=account_id)
        db.add(row)
        db.flush()
        token_id = row.id
    return {"id": token_id, "label": label, "role": role, "account_id": account_id, "token": raw_token}


def revoke_api_token(params: dict) -> dict:
    token_id = params["token_id"]
    with write_session() as db:
        row = db.get(ApiToken, token_id)
        if row is None:
            raise RuntimeError("token not found")
        from shared.models import utcnow

        row.revoked_at = utcnow()
    return {"id": token_id, "status": "revoked"}
