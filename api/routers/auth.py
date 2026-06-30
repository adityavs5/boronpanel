from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from starlette.requests import Request

from shared.db import read_session
from shared.models import PanelUser
from shared.passwords import verify_password

from api.rpc import call_daemon
from api.security import COOKIE_MAX_AGE_SECONDS, COOKIE_NAME, Identity, get_identity, sign_session_id, unsign_session_id
from api.templates import templates

router = APIRouter(tags=["auth"])


@router.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    with read_session() as db:
        user = db.scalar(select(PanelUser).where(PanelUser.username == username))
        valid = user is not None and not user.disabled and verify_password(password, user.password_hash)
        user_id = user.id if user else None
        role = user.role if user else None
        account_id = user.account_id if user else None

    if not valid:
        return templates.TemplateResponse(request, "login.html", {"error": "invalid username or password"}, status_code=401)

    # No Identity exists yet at this point -- the user is mid-authentication,
    # not authenticated -- so build one just for this audit-trail purpose
    # (panel_user_id=-1 with auth_method="session" mirrors what
    # _identity_from_bearer_token uses for tokens, which similarly have no
    # real panel_user_id).
    login_identity = Identity(panel_user_id=user_id or -1, username=username, role=role or "customer", account_id=account_id, auth_method="session")
    session_result = call_daemon("auth.create_session", login_identity, panel_user_id=user_id)
    cookie_value = sign_session_id(session_result["session_id"])

    destination = "/ui/accounts" if role == "admin" else f"/ui/accounts/{_customer_account_username(account_id)}"
    response = RedirectResponse(destination, status_code=303)
    response.set_cookie(
        COOKIE_NAME, cookie_value, max_age=COOKIE_MAX_AGE_SECONDS, httponly=True, samesite="lax", secure=True
    )
    return response


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


def _customer_account_username(account_id: int | None) -> str:
    if account_id is None:
        return ""
    from shared.models import Account

    with read_session() as db:
        account = db.get(Account, account_id)
        return account.username if account else ""
