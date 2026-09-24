"""Phase 8 feature 1: "Login as user" (admin impersonation) HTTP surface.

Three endpoints:
  - POST /api/v1/admin/accounts/{username}/impersonate  (admin) -> mint a
    single-use 5-minute token for the account.
  - POST /api/v1/impersonate/redeem  -> the issuing admin exchanges that token
    for a customer-scoped session cookie (their admin session id is captured
    so they can return).
  - POST /api/v1/impersonate/return  -> end the impersonation and restore the
    admin session cookie.

Splitting mint from redeem keeps issuance authorized on the {username} path
(require_admin + the account exists) while redemption is what captures the
admin's *current* session to restore. Both the token and every action taken
while impersonating are audit-logged (server.py dispatch()).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.requests import Request

from api.rpc import call_daemon
from api.security import (
    COOKIE_MAX_AGE_SECONDS,
    ADMIN_RETURN_COOKIE_NAME,
    COOKIE_NAME,
    Identity,
    get_identity,
    require_admin,
    sign_session_id,
    unsign_session_id,
)

admin_api_router = APIRouter(prefix="/api/v1/admin/accounts", tags=["impersonation"])
api_router = APIRouter(prefix="/api/v1/impersonate", tags=["impersonation"])


@admin_api_router.post("/{username}/impersonate")
def impersonate(username: str, identity: Identity = Depends(get_identity)):
    require_admin(identity)
    # An impersonation session is never allowed to mint a further
    # impersonation token (role is already "customer" for those, so
    # require_admin above rejects them) -- defense in depth against
    # impersonation chaining.
    result = call_daemon("impersonation.create_token", identity, username=username, admin_username=identity.username)
    return result


class RedeemBody(BaseModel):
    token: str


@api_router.post("/redeem")
def redeem(body: RedeemBody, request: Request, identity: Identity = Depends(get_identity)):
    # Must be redeemed by a real admin session (not a token, not an already
    # impersonating session): we capture that admin session's id so "Return
    # to admin" can restore it.
    require_admin(identity)
    if identity.auth_method != "session":
        raise HTTPException(status_code=400, detail="impersonation must be redeemed from an interactive admin session")
    cookie_value = request.cookies.get(COOKIE_NAME)
    admin_session_id = unsign_session_id(cookie_value) if cookie_value else None

    result = call_daemon(
        "impersonation.redeem_token",
        identity,
        token=body.token,
        admin_username=identity.username,
        admin_panel_user_id=identity.panel_user_id,
        admin_session_id=admin_session_id,
    )

    response = JSONResponse(
        {
            "status": "impersonating",
            "account_username": result["account_username"],
            "impersonator": identity.username,
        }
    )
    response.headers['Cache-Control'] = 'no-store'
    if admin_session_id:
        response.set_cookie(ADMIN_RETURN_COOKIE_NAME, sign_session_id(admin_session_id),
                            max_age=2 * 60 * 60, secure=True, httponly=True,
                            samesite='strict', path='/')
    # Swap the cookie to the new customer-scoped impersonation session. The
    # admin session is intentionally left intact (not revoked) so it can be
    # restored on return.
    response.set_cookie(
        COOKIE_NAME,
        sign_session_id(result["session_id"]),
        max_age=COOKIE_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=True,
    )
    return response


@api_router.post("/return")
def return_to_admin(request: Request, identity: Identity = Depends(get_identity)):
    if not identity.is_impersonating:
        raise HTTPException(status_code=400, detail="not currently impersonating")
    cookie_value = request.cookies.get(COOKIE_NAME)
    session_id = unsign_session_id(cookie_value) if cookie_value else None
    if session_id is None:
        raise HTTPException(status_code=400, detail="no active session to end")

    proof_cookie = request.cookies.get(ADMIN_RETURN_COOKIE_NAME)
    admin_session_id = unsign_session_id(proof_cookie) if proof_cookie else None
    result = call_daemon("impersonation.end", identity, session_id=session_id,
                         admin_session_id=admin_session_id)
    restored = bool(result.get('restored') and admin_session_id)
    response = JSONResponse({"status": "returned", "restored": restored})
    response.headers['Cache-Control'] = 'no-store'
    response.delete_cookie(ADMIN_RETURN_COOKIE_NAME, secure=True, httponly=True,
                           samesite='strict', path='/')
    if restored:
        # Restore only the independently supplied credential, after root has
        # checked its owner, expiry, revocation and administrator status.
        response.set_cookie(
            COOKIE_NAME,
            sign_session_id(admin_session_id),
            max_age=COOKIE_MAX_AGE_SECONDS,
            httponly=True,
            samesite="lax",
            secure=True,
        )
    else:
        response.delete_cookie(COOKIE_NAME, secure=True, httponly=True, samesite="lax")
    return response
