"""Root-owned panel authentication and credential state.

borond verifies passwords and second factors before minting browser sessions.
Legacy mutation helpers remain for root-internal use and are blocked at the
public RPC policy boundary.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import string

from sqlalchemy import func, select, text

from shared.db import write_session
from shared.models import Account, ApiToken, LoginAttempt, LoginChallenge, PanelUser, Session, TotpCredential, utcnow
from shared.passwords import hash_password, verify_password
from shared.validation import ValidationError, generate_strong_password, validate_password_strength, validate_username
from shared.session_ids import session_digest

from daemon import events

SESSION_TTL_HOURS = 24 * 7
TOKEN_PREFIX_LEN = 8

# Security audit finding F2: no throttling existed on /login at all --
# unlimited password attempts against any username, flagged as a known
# gap since Phase 1 and never revisited. 5 failures locks that username
# out for 15 minutes; a successful login clears the counter. Scoped per
# username rather than per-IP: this project has no established
# trusted-proxy model to make client-IP attribution reliable, and
# per-username lockout is the same primary mechanism cPanel's own
# cphulk uses. 15 minutes (not longer) deliberately bounds the
# self-inflicted-lockout annoyance of a third party repeatedly failing
# against a known username, while still meaningfully slowing automated
# guessing.
LOCKOUT_THRESHOLD = 5
LOCKOUT_DURATION_SECONDS = 15 * 60


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

    if role not in ("admin", "customer", "reseller"):
        raise ValidationError("role must be 'admin', 'reseller', or 'customer'")
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


def list_administrators(params: dict | None = None) -> dict:
    with write_session() as session:
        rows = session.scalars(
            select(PanelUser).where(PanelUser.role == "admin").order_by(PanelUser.username)
        ).all()
        return {"administrators": [_panel_user_dict(row) for row in rows]}


def create_administrator(params: dict) -> dict:
    username = validate_username(params["username"])
    password = params.get("password") or generate_strong_password()
    result = create_panel_user({"username": username, "password": password, "role": "admin"})
    result["initial_password"] = password
    return result


def set_administrator_status(params: dict) -> dict:
    username = validate_username(params["username"])
    actor_username = str(params.get("actor_username") or "")
    disabled = bool(params["disabled"])
    with write_session() as session:
        user = session.scalar(select(PanelUser).where(PanelUser.username == username, PanelUser.role == "admin"))
        if user is None:
            raise RuntimeError(f"administrator '{username}' not found")
        if disabled and username == actor_username:
            raise ValidationError("you cannot disable your own administrator login")
        if disabled and not user.disabled:
            enabled_count = session.scalar(select(func.count()).select_from(PanelUser).where(
                PanelUser.role == "admin", PanelUser.disabled.is_(False)
            )) or 0
            if enabled_count <= 1:
                raise ValidationError("at least one administrator must remain enabled")
        user.disabled = disabled
        if disabled:
            for row in session.scalars(select(Session).where(
                Session.panel_user_id == user.id, Session.revoked.is_(False)
            )).all():
                row.revoked = True
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
        session.flush()
        # Security audit finding F11: a password change is frequently
        # motivated by "I think someone else has my session" -- that's
        # not actually addressed unless existing sessions are revoked
        # too, not just the credential. The caller's own already-resolved
        # Identity for *this* request is unaffected (FastAPI resolved it
        # before this RPC was made); their *next* request requires a
        # fresh login with the new password, same as changing a password
        # anywhere else typically behaves.
        active_sessions = session.scalars(
            select(Session).where(Session.panel_user_id == user.id, Session.revoked == False)  # noqa: E712
        ).all()
        for row in active_sessions:
            row.revoked = True
        return {"username": username, "status": "password_changed"}


def check_login_lockout(params: dict) -> dict:
    """Security audit finding F2, hardened against the check-then-act race the
    original two-call (read-only check + separate increment) design had: enough
    concurrent attempts all passed a stale read-only check before any of them
    recorded a failure, so the lock only ever engaged for the *next* burst --
    letting one burst try N passwords where N is the concurrency.

    This is now the atomic gate. It RESERVES one attempt: under the row's write
    lock it increments the failure counter and returns locked=True as soon as
    the threshold is exceeded, so the (THRESHOLD+1)-th concurrent attempt is
    refused *before* boron-api spends a bcrypt/TOTP verify on it. A
    subsequent successful verification calls record_login_result(success=True)
    to clear the counter; a failed one leaves the reservation counted (no
    second increment needed -- and no double count). Called before password
    verification AND before the 2FA-code check, so both steps are throttled by
    the same per-username budget."""
    username = params["username"]
    now = dt.datetime.now(dt.timezone.utc)
    with write_session() as session:
        # SQLite SELECT does not acquire a row write lock. Reserve the
        # transaction before reading so parallel attempts cannot lose counts
        # or race the initial unique username insert.
        session.execute(text("BEGIN IMMEDIATE"))
        row = session.scalar(select(LoginAttempt).where(LoginAttempt.username == username))
        if row is not None and row.locked_until is not None:
            locked_until = row.locked_until
            if locked_until.tzinfo is None:
                locked_until = locked_until.replace(tzinfo=dt.timezone.utc)
            if locked_until > now:
                return {"locked": True, "retry_after_seconds": int((locked_until - now).total_seconds())}
            # Lock expired -- start a fresh window.
            row.locked_until = None
            row.failed_count = 0
        if row is None:
            row = LoginAttempt(username=username, failed_count=0)
            session.add(row)
            session.flush()
        row.failed_count += 1
        if row.failed_count > LOCKOUT_THRESHOLD:
            row.locked_until = now + dt.timedelta(seconds=LOCKOUT_DURATION_SECONDS)
            row.failed_count = 0
            return {"locked": True, "retry_after_seconds": LOCKOUT_DURATION_SECONDS}
        return {"locked": False}


def record_login_result(params: dict) -> dict:
    """Finalize an attempt reserved by check_login_lockout. Success clears the
    counter so a legitimate user who eventually authenticates isn't penalized
    for earlier typos. A failure needs no action here: the attempt was already
    counted at reservation time, so skipping it avoids the double-count the old
    check-then-record split would now introduce."""
    username = params["username"]
    success = bool(params["success"])
    if not success:
        return {"status": "ok"}
    with write_session() as session:
        row = session.scalar(select(LoginAttempt).where(LoginAttempt.username == username))
        if row is not None:
            session.delete(row)
    return {"status": "ok"}


def create_session(params: dict) -> dict:
    panel_user_id = params["panel_user_id"]
    session_id = secrets.token_urlsafe(32)
    expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=SESSION_TTL_HOURS)
    with write_session() as db:
        user = db.get(PanelUser, panel_user_id)
        if user is None or user.disabled:
            raise RuntimeError("panel user not found or disabled")
        row = Session(session_id=session_digest(session_id), panel_user_id=panel_user_id, expires_at=expires_at)
        db.add(row)
        # Phase 7b feature 3: "new login to customer panel" notification --
        # admin logins are deliberately excluded (goal names only the
        # *customer* panel; an admin has no AccountNotificationPrefs row to
        # notify anyway, since that table is keyed by account, not by
        # panel_user). account_snapshot is read inside this same session
        # so it's fully loaded before the session closes.
        account_snapshot = None
        if user.role == "customer" and user.account_id is not None:
            account_snapshot = db.get(Account, user.account_id)
    if account_snapshot is not None:
        events.emit("login.new", account_snapshot)
    return {"session_id": session_id, "expires_at": expires_at.isoformat()}


def login_begin(params: dict) -> dict:
    """The only unauthenticated session path: root checks the password first."""
    username = params.get("username")
    password = params.get("password")
    if not isinstance(username, str) or not isinstance(password, str) or not username:
        raise ValidationError("invalid login request")
    gate = check_login_lockout({"username": username})
    if gate["locked"]:
        return {"locked": True, "retry_after_seconds": gate["retry_after_seconds"]}
    with write_session() as db:
        user = db.scalar(select(PanelUser).where(PanelUser.username == username))
        valid = user is not None and not user.disabled and verify_password(password, user.password_hash)
        if not valid:
            return {"valid": False}
        has_totp = db.scalar(select(TotpCredential.id).where(
            TotpCredential.panel_user_id == user.id, TotpCredential.enabled.is_(True),
        )) is not None
        user_id = user.id
        role = user.role
        account_id = user.account_id
        if has_totp:
            raw = secrets.token_urlsafe(32)
            db.add(LoginChallenge(token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                                  panel_user_id=user_id, password_hash=user.password_hash,
                                  expires_at=utcnow() + dt.timedelta(minutes=5)))
    if has_totp:
        return {"needs_2fa": True, "pending_token": raw,
                "role": role, "account_id": account_id, "panel_user_id": user_id}
    record_login_result({"username": username, "success": True})
    result = create_session({"panel_user_id": user_id})
    return {"valid": True, "role": role, "account_id": account_id, **result}


def login_finish(params: dict) -> dict:
    """Consume a root-issued challenge after root verifies TOTP or recovery."""
    raw = params.get("pending_token")
    code = params.get("code")
    if not isinstance(raw, str) or not isinstance(code, str) or not raw:
        raise ValidationError("invalid second-factor request")
    digest = hashlib.sha256(raw.encode()).hexdigest()
    with write_session() as db:
        challenge = db.scalar(select(LoginChallenge).where(LoginChallenge.token_hash == digest))
        now = utcnow()
        if challenge is None or challenge.used_at is not None or challenge.expires_at.replace(tzinfo=dt.timezone.utc) <= now:
            return {"valid": False}
        user = db.get(PanelUser, challenge.panel_user_id)
        if user is None or user.disabled or user.password_hash != challenge.password_hash:
            return {"valid": False}
        username, user_id, role, account_id = user.username, user.id, user.role, user.account_id
    gate = check_login_lockout({"username": username})
    if gate["locked"]:
        return {"locked": True, "retry_after_seconds": gate["retry_after_seconds"]}
    from daemon.totp import check_login_code

    if not check_login_code({"panel_user_id": user_id, "code": code})["valid"]:
        return {"valid": False}
    from sqlalchemy import update

    with write_session() as db:
        consumed = db.execute(update(LoginChallenge).where(
            LoginChallenge.token_hash == digest, LoginChallenge.used_at.is_(None),
        ).values(used_at=utcnow()))
        if consumed.rowcount != 1:
            return {"valid": False}
    record_login_result({"username": username, "success": True})
    result = create_session({"panel_user_id": user_id})
    return {"valid": True, "role": role, "account_id": account_id, **result}


def revoke_session(params: dict) -> dict:
    session_id = params["session_id"]
    with write_session() as db:
        row = db.scalar(select(Session).where(Session.session_id == session_digest(session_id)))
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
    return {
        "id": token_id,
        "label": label,
        "role": role,
        "account_id": account_id,
        "token": raw_token,
        "expires_at": (utcnow() + dt.timedelta(days=90)).isoformat(),
    }


def revoke_api_token(params: dict) -> dict:
    token_id = params["token_id"]
    with write_session() as db:
        row = db.get(ApiToken, token_id)
        if row is None:
            raise RuntimeError("token not found")
        from shared.models import utcnow

        row.revoked_at = utcnow()
    return {"id": token_id, "status": "revoked"}
