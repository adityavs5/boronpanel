from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from starlette.requests import Request

from shared.db import read_session
from shared.models import PanelUser
from shared.passwords import verify_password

from api.rpc import call_daemon
from api.security import (
    COOKIE_MAX_AGE_SECONDS,
    COOKIE_NAME,
    Identity,
    get_identity,
    sign_session_id,
    sign_twofactor_pending,
    unsign_session_id,
    unsign_twofactor_pending,
)
from api.templates import templates

router = APIRouter(tags=["auth"])


@router.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    with read_session() as db:
        user = db.scalar(select(PanelUser).where(PanelUser.username == username))
        user_id = user.id if user else None
        role = user.role if user else None
        account_id = user.account_id if user else None
        disabled = user.disabled if user else False
        password_hash = user.password_hash if user else None

    # No Identity exists yet at this point -- the user is mid-authentication,
    # not authenticated -- so build one just for this audit-trail purpose
    # (panel_user_id=-1 with auth_method="session" mirrors what
    # _identity_from_bearer_token uses for tokens, which similarly have no
    # real panel_user_id).
    login_identity = Identity(panel_user_id=user_id or -1, username=username, role=role or "customer", account_id=account_id, auth_method="session")

    # Security audit finding F2: brute-force throttling, checked before
    # spending a bcrypt verify -- 5 failed attempts locks this username
    # out for 15 minutes.
    lockout = call_daemon("auth.check_login_lockout", login_identity, username=username)
    if lockout.get("locked"):
        retry_minutes = lockout.get("retry_after_seconds", 0) // 60 + 1
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": f"too many failed attempts -- try again in {retry_minutes} minute(s)"},
            status_code=429,
        )

    valid = user is not None and not disabled and verify_password(password, password_hash or "")
    call_daemon("auth.record_login_result", login_identity, username=username, success=valid)

    if not valid:
        return templates.TemplateResponse(request, "login.html", {"error": "invalid username or password"}, status_code=401)

    # Phase 5 feature 10: TOTP 2FA. Password is correct, but if this user
    # has 2FA enabled the session must not be created yet -- a second
    # step (code or recovery code) is required first.
    totp_status = call_daemon("totp.status", login_identity, panel_user_id=user_id)
    if totp_status["enabled"]:
        pending_token = sign_twofactor_pending(user_id)
        return templates.TemplateResponse(request, "twofactor_login.html", {"pending_token": pending_token, "error": None})

    return _complete_login(user_id, role, account_id)


def _complete_login(user_id: int, role: str, account_id: int | None):
    login_identity = Identity(panel_user_id=user_id, username="", role=role, account_id=account_id, auth_method="session")
    session_result = call_daemon("auth.create_session", login_identity, panel_user_id=user_id)
    cookie_value = sign_session_id(session_result["session_id"])

    destination = "/ui/accounts" if role == "admin" else f"/ui/accounts/{_customer_account_username(account_id)}"
    response = RedirectResponse(destination, status_code=303)
    response.set_cookie(
        COOKIE_NAME, cookie_value, max_age=COOKIE_MAX_AGE_SECONDS, httponly=True, samesite="lax", secure=True
    )
    return response


@router.post("/login/2fa")
def login_2fa_submit(request: Request, pending_token: str = Form(...), code: str = Form(...)):
    panel_user_id = unsign_twofactor_pending(pending_token)
    if panel_user_id is None:
        return templates.TemplateResponse(request, "login.html", {"error": "2FA session expired -- please log in again"}, status_code=401)

    with read_session() as db:
        user = db.get(PanelUser, panel_user_id)
        if user is None or user.disabled:
            return templates.TemplateResponse(request, "login.html", {"error": "invalid username or password"}, status_code=401)
        role, account_id = user.role, user.account_id

    check_identity = Identity(panel_user_id=panel_user_id, username=user.username, role=role, account_id=account_id, auth_method="session")
    result = call_daemon("totp.check_login_code", check_identity, panel_user_id=panel_user_id, code=code)
    if not result["valid"]:
        return templates.TemplateResponse(
            request, "twofactor_login.html", {"pending_token": pending_token, "error": "invalid code"}, status_code=401
        )

    return _complete_login(panel_user_id, role, account_id)


@router.post("/logout")
def logout(request: Request, identity: Identity = Depends(get_identity)):
    cookie_value = request.cookies.get(COOKIE_NAME)
    if cookie_value:
        session_id = unsign_session_id(cookie_value)
        if session_id:
            call_daemon("auth.revoke_session", identity, session_id=session_id)

    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/change-password")
def change_password_form(request: Request, identity: Identity = Depends(get_identity)):
    return templates.TemplateResponse(request, "change_password.html", {"identity": identity, "error": None})


@router.post("/change-password")
def change_password_submit(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    identity: Identity = Depends(get_identity),
):
    # Self-service, own-account only: the target is always identity's own
    # username, never taken from the form -- a bearer-token/session
    # identity that's been compromised must not be able to use this
    # endpoint to change any *other* panel user's password, and requiring
    # the current password (re-verified here, the same way login itself
    # already checks it) means a hijacked but not-yet-fully-controlled
    # session can't silently lock the real owner out either.
    with read_session() as db:
        user = db.scalar(select(PanelUser).where(PanelUser.username == identity.username))
        current_ok = user is not None and verify_password(current_password, user.password_hash)

    if not current_ok:
        return templates.TemplateResponse(
            request, "change_password.html", {"identity": identity, "error": "current password is incorrect"}, status_code=401
        )
    if new_password != confirm_password:
        return templates.TemplateResponse(
            request, "change_password.html", {"identity": identity, "error": "new password and confirmation do not match"}, status_code=400
        )

    try:
        call_daemon("panel_user.set_password", identity, username=identity.username, password=new_password)
    except HTTPException as exc:
        return templates.TemplateResponse(
            request, "change_password.html", {"identity": identity, "error": exc.detail}, status_code=exc.status_code
        )

    return templates.TemplateResponse(request, "change_password.html", {"identity": identity, "error": None, "success": True})


def _customer_account_username(account_id: int | None) -> str:
    if account_id is None:
        return ""
    from shared.models import Account

    with read_session() as db:
        account = db.get(Account, account_id)
        return account.username if account else ""
