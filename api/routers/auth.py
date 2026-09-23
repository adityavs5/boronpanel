from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from starlette.requests import Request

from shared.db import read_session
from shared.models import Account, PanelUser
from shared.passwords import verify_password

from api.rpc import call_daemon, call_daemon_anonymous
from api.security import (
    COOKIE_MAX_AGE_SECONDS,
    COOKIE_NAME,
    Identity,
    get_identity,
    sign_session_id,
    unsign_session_id,
)

router = APIRouter(tags=["auth"])


def _reject_cross_origin_form(request: Request) -> None:
    """Reject browser form posts originating at a different origin.

    Origin is emitted for modern cross-site POSTs; Referer is the fallback.
    Requests with neither header remain compatible with non-browser clients.
    """
    expected = f"{request.url.scheme}://{request.url.netloc}"
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != expected:
        raise HTTPException(status_code=403, detail="cross-origin form submission rejected")
    referer = request.headers.get("referer")
    if not origin and referer and not (referer == expected or referer.startswith(expected + "/")):
        raise HTTPException(status_code=403, detail="cross-origin form submission rejected")


@router.get("/login")
def login_form():
    # The interactive login UI is the React SPA. Keep GET /login as a
    # convenience entry point that lands on the SPA's login route.
    return RedirectResponse("/app/login", status_code=307)


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    _reject_cross_origin_form(request)
    result = call_daemon_anonymous("auth.login.begin", username=username, password=password)
    if result.get("locked"):
        retry_minutes = result.get("retry_after_seconds", 0) // 60 + 1
        return JSONResponse(
            {"detail": f"too many failed attempts -- try again in {retry_minutes} minute(s)"},
            status_code=429,
        )
    if not result.get("valid") and not result.get("needs_2fa"):
        return JSONResponse({"detail": "invalid username or password"}, status_code=401)
    from api.security import enforce_listener_role
    login_identity = Identity(result.get("panel_user_id", -1), username, result["role"],
                              result.get("account_id"), "session")
    enforce_listener_role(login_identity, request)
    if result.get("needs_2fa"):
        return JSONResponse({"needs_2fa": True, "pending_token": result["pending_token"]})
    return _complete_login(result["session_id"])


def _complete_login(session_id: str):
    cookie_value = sign_session_id(session_id)

    # Land in the React SPA. The SPA calls GET /api/v1/whoami after login to
    # resolve role + account, so it no longer depends on parsing this redirect.
    response = RedirectResponse("/app", status_code=303)
    response.set_cookie(
        COOKIE_NAME, cookie_value, max_age=COOKIE_MAX_AGE_SECONDS, httponly=True, samesite="lax", secure=True
    )
    return response


@router.get("/api/v1/whoami")
def whoami(identity: Identity = Depends(get_identity)):
    """Current-session identity for the SPA. For customers, `username` is the
    hosting-account username (what the account-scoped API paths use), not the
    panel login name; admins have no bound account."""
    if identity.role in ("admin", "reseller"):
        return {"role": identity.role, "username": identity.username, "account_username": None, "impersonating": False}
    account_username = _customer_account_username(identity.account_id)
    # Phase 8 feature 1: surface impersonation so the SPA can show a persistent
    # "Return to admin" banner even across a hard refresh (the flag is derived
    # server-side from the session, never trusted from the client).
    return {
        "role": "customer",
        "username": account_username,
        "account_username": account_username,
        "impersonating": identity.is_impersonating,
        "impersonator": identity.impersonator,
    }


@router.post("/login/2fa")
def login_2fa_submit(request: Request, pending_token: str = Form(...), code: str = Form(...)):
    _reject_cross_origin_form(request)
    result = call_daemon_anonymous("auth.login.finish", pending_token=pending_token, code=code)
    if result.get("locked"):
        retry_minutes = result.get("retry_after_seconds", 0) // 60 + 1
        return JSONResponse(
            {"detail": f"too many failed attempts -- try again in {retry_minutes} minute(s)"},
            status_code=429,
        )
    if not result.get("valid"):
        return JSONResponse({"detail": "invalid code"}, status_code=401)
    from api.security import enforce_listener_role
    enforce_listener_role(Identity(-1, "", result["role"], result.get("account_id"), "session"), request)
    return _complete_login(result["session_id"])


@router.post("/logout")
def logout(request: Request, identity: Identity = Depends(get_identity)):
    cookie_value = request.cookies.get(COOKIE_NAME)
    if cookie_value:
        session_id = unsign_session_id(cookie_value)
        if session_id:
            call_daemon("auth.revoke_session", identity, session_id=session_id)

    response = JSONResponse({"status": "logged_out"})
    response.delete_cookie(COOKIE_NAME)
    return response


@router.post("/change-password")
def change_password_submit(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    identity: Identity = Depends(get_identity),
):
    _reject_cross_origin_form(request)
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
        return JSONResponse({"detail": "current password is incorrect"}, status_code=401)
    if new_password != confirm_password:
        return JSONResponse({"detail": "new password and confirmation do not match"}, status_code=400)

    try:
        call_daemon("panel_user.set_password", identity, username=identity.username,
                    password=new_password, current_password=current_password)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    return JSONResponse({"status": "password_changed"})


def _customer_account_username(account_id: int | None) -> str:
    if account_id is None:
        return ""
    with read_session() as db:
        account = db.get(Account, account_id)
        return account.username if account else ""
