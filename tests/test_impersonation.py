"""Phase 8 feature 1: login-as-user (admin impersonation)."""
import datetime as dt

import pytest
from sqlalchemy import select

from daemon import handlers_auth as hauth
from daemon import impersonation
from shared.db import write_session
from shared.models import (
    Account,
    ImpersonationSession,
    ImpersonationToken,
    Session as SessionModel,
    utcnow,
)


def _make_account(username="demo1", status="active"):
    with write_session() as db:
        account = Account(username=username, status=status)
        db.add(account)
        db.flush()
        return account.id


def _make_admin(username="admin"):
    return hauth.create_panel_user({"username": username, "password": "SuperSecret123!", "role": "admin"})


# --- create_token ----------------------------------------------------------


def test_create_token_returns_raw_and_stores_hashed(isolated_db):
    _make_account("demo1")
    result = impersonation.create_token({"username": "demo1", "admin_username": "admin"})
    assert result["token"].startswith("fh_imp_")
    assert result["account_username"] == "demo1"
    assert result["expires_in_seconds"] == 300

    with write_session() as db:
        row = db.scalar(select(ImpersonationToken))
        assert row is not None
        assert row.token_hash != result["token"]  # never stored raw
        assert row.admin_username == "admin"
        assert row.used_at is None


def test_create_token_rejects_missing_account(isolated_db):
    with pytest.raises(RuntimeError):
        impersonation.create_token({"username": "nope", "admin_username": "admin"})


def test_create_token_rejects_terminated_account(isolated_db):
    _make_account("gone", status="terminated")
    with pytest.raises(RuntimeError):
        impersonation.create_token({"username": "gone", "admin_username": "admin"})


def test_create_token_allows_suspended_account(isolated_db):
    _make_account("susp", status="suspended")
    result = impersonation.create_token({"username": "susp", "admin_username": "admin"})
    assert result["account_username"] == "susp"


# --- redeem_token ----------------------------------------------------------


def _issue(username="demo1", admin_username="admin"):
    return impersonation.create_token({"username": username, "admin_username": admin_username})["token"]


def test_redeem_creates_customer_session_and_marks_used(isolated_db):
    account_id = _make_account("demo1")
    admin = _make_admin()
    token = _issue()

    result = impersonation.redeem_token(
        {
            "token": token,
            "admin_username": "admin",
            "admin_panel_user_id": admin["id"],
            "admin_session_id": "admin-sess-1",
        }
    )
    assert result["account_username"] == "demo1"
    session_id = result["session_id"]

    with write_session() as db:
        # session created, owned by the admin panel user
        sess = db.scalar(select(SessionModel).where(SessionModel.session_id == session_id))
        assert sess is not None
        assert sess.panel_user_id == admin["id"]
        assert sess.revoked is False
        # impersonation record links it to the account + admin session to restore
        imp = db.scalar(select(ImpersonationSession).where(ImpersonationSession.session_id == session_id))
        assert imp.account_id == account_id
        assert imp.admin_session_id == "admin-sess-1"
        assert imp.ended_at is None
        # token consumed
        tok = db.scalar(select(ImpersonationToken))
        assert tok.used_at is not None


def test_redeem_is_single_use(isolated_db):
    _make_account("demo1")
    admin = _make_admin()
    token = _issue()
    impersonation.redeem_token({"token": token, "admin_username": "admin", "admin_panel_user_id": admin["id"]})
    with pytest.raises(RuntimeError, match="already been used"):
        impersonation.redeem_token({"token": token, "admin_username": "admin", "admin_panel_user_id": admin["id"]})


def test_redeem_rejects_expired_token(isolated_db):
    _make_account("demo1")
    admin = _make_admin()
    token = _issue()
    with write_session() as db:
        row = db.scalar(select(ImpersonationToken))
        row.expires_at = utcnow() - dt.timedelta(seconds=1)
    with pytest.raises(RuntimeError, match="expired"):
        impersonation.redeem_token({"token": token, "admin_username": "admin", "admin_panel_user_id": admin["id"]})


def test_redeem_rejects_wrong_admin(isolated_db):
    _make_account("demo1")
    other = _make_admin("admin2")
    token = _issue(admin_username="admin")  # issued by "admin"
    with pytest.raises(RuntimeError, match="different admin"):
        impersonation.redeem_token({"token": token, "admin_username": "admin2", "admin_panel_user_id": other["id"]})


def test_redeem_rejects_invalid_token(isolated_db):
    _make_account("demo1")
    admin = _make_admin()
    with pytest.raises(RuntimeError, match="invalid"):
        impersonation.redeem_token({"token": "fh_imp_bogus", "admin_username": "admin", "admin_panel_user_id": admin["id"]})


def test_redeem_rejects_non_admin_redeemer(isolated_db):
    account_id = _make_account("demo1")
    customer = hauth.create_panel_user(
        {"username": "cust1", "password": "SuperSecret123!", "role": "customer", "account_id": account_id}
    )
    token = _issue(admin_username="cust1")  # pretend it was issued to cust1
    with pytest.raises(RuntimeError, match="not an admin"):
        impersonation.redeem_token({"token": token, "admin_username": "cust1", "admin_panel_user_id": customer["id"]})


# --- end -------------------------------------------------------------------


def test_end_revokes_session_and_returns_admin_session(isolated_db):
    _make_account("demo1")
    admin = _make_admin()
    token = _issue()
    redeemed = impersonation.redeem_token(
        {"token": token, "admin_username": "admin", "admin_panel_user_id": admin["id"], "admin_session_id": "admin-sess-1"}
    )
    session_id = redeemed["session_id"]

    result = impersonation.end({"session_id": session_id})
    assert result["admin_session_id"] == "admin-sess-1"
    assert result["status"] == "ended"

    with write_session() as db:
        sess = db.scalar(select(SessionModel).where(SessionModel.session_id == session_id))
        assert sess.revoked is True
        imp = db.scalar(select(ImpersonationSession).where(ImpersonationSession.session_id == session_id))
        assert imp.ended_at is not None


def test_end_idempotent_on_unknown_session(isolated_db):
    result = impersonation.end({"session_id": "no-such-session"})
    assert result["admin_session_id"] is None
    assert result["status"] == "not_impersonating"


# --- get_identity downscoping (the security-critical part) ------------------


def test_identity_downscopes_impersonation_to_customer(isolated_db):
    from api import security

    account_id = _make_account("demo1")
    admin = _make_admin()
    token = _issue()
    redeemed = impersonation.redeem_token(
        {"token": token, "admin_username": "admin", "admin_panel_user_id": admin["id"], "admin_session_id": "s"}
    )
    cookie = security.sign_session_id(redeemed["session_id"])
    identity = security._identity_from_session_cookie(cookie)

    assert identity is not None
    assert identity.role == "customer"  # NOT admin, even though session is admin-owned
    assert identity.account_id == account_id
    assert identity.is_impersonating is True
    assert identity.impersonator == "admin"
    assert identity.impersonated_account == "demo1"


def test_identity_admin_session_not_impersonating(isolated_db):
    from api import security

    admin = _make_admin()
    sess = hauth.create_session({"panel_user_id": admin["id"]})
    cookie = security.sign_session_id(sess["session_id"])
    identity = security._identity_from_session_cookie(cookie)

    assert identity.role == "admin"
    assert identity.is_impersonating is False


def test_ended_impersonation_session_is_dead(isolated_db):
    """After returning to admin, the impersonation cookie must resolve to
    nothing (the Session is revoked), not silently keep customer access."""
    from api import security

    _make_account("demo1")
    admin = _make_admin()
    token = _issue()
    redeemed = impersonation.redeem_token(
        {"token": token, "admin_username": "admin", "admin_panel_user_id": admin["id"]}
    )
    impersonation.end({"session_id": redeemed["session_id"]})

    cookie = security.sign_session_id(redeemed["session_id"])
    assert security._identity_from_session_cookie(cookie) is None
