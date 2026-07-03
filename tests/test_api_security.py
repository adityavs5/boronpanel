"""Tests for api/security.py -- the auth/RBAC layer forgehost-api enforces
on every request. Run against the same isolated SQLite fixture the daemon
tests use; api.security's read_session() points at shared.config.settings
the same way shared.db.write_session() does, so isolated_db covers both.
"""
import datetime as dt

import pytest

pytest.importorskip("fastapi")

from api import security as sec  # noqa: E402
from daemon import handlers_account as ha  # noqa: E402
from daemon import handlers_auth as hauth  # noqa: E402
from shared.db import write_session  # noqa: E402
from shared.models import Account, ApiToken, PanelUser, Session  # noqa: E402


def _make_admin(isolated_db):
    return hauth.create_panel_user({"username": "admin1", "password": "AdminPass123!", "role": "admin"})


def _make_account_and_customer(isolated_db, monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    account = ha.create_account({"username": "cust1"})
    user = hauth.create_panel_user(
        {"username": "custlogin", "password": "CustPass123!", "role": "customer", "account_id": account["id"]}
    )
    return account, user


def test_sign_and_unsign_round_trip(isolated_db):
    signed = sec.sign_session_id("abc123")
    assert sec.unsign_session_id(signed) == "abc123"


def test_unsign_rejects_tampered_cookie(isolated_db):
    # Rarely flaky when tampering only the very last character: itsdangerous's
    # signature is base64-encoded, and a base64 string whose bit-length isn't
    # a multiple of 6 has a final character where 1-2 bits are unused padding
    # -- occasionally (timestamp-dependent, since dumps() embeds the current
    # time) two different characters there decode to the identical
    # underlying signature bytes, letting a "tampered" cookie still verify.
    # Tampering the payload instead (well before the trailing signature) has
    # no such edge case -- it always changes the actual signed bytes.
    signed = sec.sign_session_id("abc123")
    tampered = ("x" if signed[0] != "x" else "y") + signed[1:]
    assert sec.unsign_session_id(tampered) is None


def test_unsign_rejects_garbage(isolated_db):
    assert sec.unsign_session_id("not-a-valid-cookie-at-all") is None


def test_identity_from_valid_session_cookie(isolated_db):
    admin = _make_admin(isolated_db)
    session_result = hauth.create_session({"panel_user_id": admin["id"]})
    cookie = sec.sign_session_id(session_result["session_id"])

    identity = sec._identity_from_session_cookie(cookie)
    assert identity is not None
    assert identity.username == "admin1"
    assert identity.role == "admin"
    assert identity.auth_method == "session"


def test_identity_from_revoked_session_is_none(isolated_db):
    admin = _make_admin(isolated_db)
    session_result = hauth.create_session({"panel_user_id": admin["id"]})
    hauth.revoke_session({"session_id": session_result["session_id"]})
    cookie = sec.sign_session_id(session_result["session_id"])

    assert sec._identity_from_session_cookie(cookie) is None


def test_identity_from_expired_session_is_none(isolated_db):
    admin = _make_admin(isolated_db)
    session_result = hauth.create_session({"panel_user_id": admin["id"]})
    with write_session() as db:
        row = db.scalar(
            __import__("sqlalchemy").select(Session).where(Session.session_id == session_result["session_id"])
        )
        row.expires_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
    cookie = sec.sign_session_id(session_result["session_id"])

    assert sec._identity_from_session_cookie(cookie) is None


def test_identity_from_disabled_user_is_none(isolated_db):
    admin = _make_admin(isolated_db)
    session_result = hauth.create_session({"panel_user_id": admin["id"]})
    with write_session() as db:
        user = db.get(PanelUser, admin["id"])
        user.disabled = True
    cookie = sec.sign_session_id(session_result["session_id"])

    assert sec._identity_from_session_cookie(cookie) is None


def test_identity_from_bearer_token(isolated_db):
    result = hauth.create_api_token({"label": "test-integration", "role": "admin"})
    identity = sec._identity_from_bearer_token(result["token"])
    assert identity is not None
    assert identity.role == "admin"
    assert identity.auth_method == "token"


def test_identity_from_revoked_token_is_none(isolated_db):
    result = hauth.create_api_token({"label": "test-integration", "role": "admin"})
    hauth.revoke_api_token({"token_id": result["id"]})
    assert sec._identity_from_bearer_token(result["token"]) is None


def test_identity_from_garbage_token_is_none(isolated_db):
    assert sec._identity_from_bearer_token("not-a-real-token") is None


def test_require_admin_allows_admin():
    identity = sec.Identity(1, "admin1", "admin", None, "session")
    sec.require_admin(identity)  # should not raise


def test_require_admin_rejects_customer():
    from fastapi import HTTPException

    identity = sec.Identity(1, "cust1", "customer", 5, "session")
    with pytest.raises(HTTPException) as exc_info:
        sec.require_admin(identity)
    assert exc_info.value.status_code == 403


def test_require_account_access_admin_always_allowed():
    identity = sec.Identity(1, "admin1", "admin", None, "session")
    sec.require_account_access(identity, "anyone")  # should not raise


def test_require_account_access_customer_own_account(isolated_db, monkeypatch):
    account, user = _make_account_and_customer(isolated_db, monkeypatch)
    identity = sec.Identity(user["id"], "custlogin", "customer", account["id"], "session")
    sec.require_account_access(identity, "cust1")  # should not raise


def test_require_account_access_customer_other_account_denied(isolated_db, monkeypatch):
    from fastapi import HTTPException

    account, user = _make_account_and_customer(isolated_db, monkeypatch)
    identity = sec.Identity(user["id"], "custlogin", "customer", account["id"], "session")
    with pytest.raises(HTTPException) as exc_info:
        sec.require_account_access(identity, "someone-elses-account")
    assert exc_info.value.status_code == 403


def test_require_domain_access_customer_own_domain(isolated_db, monkeypatch):
    account, user = _make_account_and_customer(isolated_db, monkeypatch)
    from shared.models import Domain

    with write_session() as db:
        db.add(Domain(account_id=account["id"], domain="cust1.example", kind="primary", docroot="/home/cust1/public_html"))

    identity = sec.Identity(user["id"], "custlogin", "customer", account["id"], "session")
    sec.require_domain_access(identity, "cust1.example")  # should not raise


def test_require_domain_access_customer_other_domain_denied(isolated_db, monkeypatch):
    from fastapi import HTTPException

    account, user = _make_account_and_customer(isolated_db, monkeypatch)
    identity = sec.Identity(user["id"], "custlogin", "customer", account["id"], "session")
    with pytest.raises(HTTPException) as exc_info:
        sec.require_domain_access(identity, "nonexistent-or-other.example")
    assert exc_info.value.status_code == 403


def test_require_customer_self_access_allows_own_customer(isolated_db, monkeypatch):
    account, user = _make_account_and_customer(isolated_db, monkeypatch)
    identity = sec.Identity(user["id"], "custlogin", "customer", account["id"], "session")
    sec.require_customer_self_access(identity, "cust1")  # should not raise


def test_require_customer_self_access_rejects_other_customer(isolated_db, monkeypatch):
    from fastapi import HTTPException

    account, user = _make_account_and_customer(isolated_db, monkeypatch)
    identity = sec.Identity(user["id"], "custlogin", "customer", account["id"], "session")
    with pytest.raises(HTTPException) as exc_info:
        sec.require_customer_self_access(identity, "someone-elses-account")
    assert exc_info.value.status_code == 403


def test_require_customer_self_access_rejects_admin():
    """Phase 4 feature 6's explicit "customer panel only" scoping -- the
    one resource in this project where an admin identity is rejected even
    though it would pass every other require_*_access check."""
    from fastapi import HTTPException

    identity = sec.Identity(1, "admin1", "admin", None, "session")
    with pytest.raises(HTTPException) as exc_info:
        sec.require_customer_self_access(identity, "cust1")
    assert exc_info.value.status_code == 403
