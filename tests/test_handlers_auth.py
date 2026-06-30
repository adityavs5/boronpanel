import pytest

from daemon import handlers_auth as hauth
from shared.passwords import verify_password
from shared.validation import ValidationError


def test_create_panel_user_admin(isolated_db):
    result = hauth.create_panel_user({"username": "admin", "password": "supersecret123", "role": "admin"})
    assert result["role"] == "admin"
    assert result["account_id"] is None


def test_create_panel_user_customer_requires_account_id(isolated_db):
    with pytest.raises(ValidationError):
        hauth.create_panel_user({"username": "cust1", "password": "supersecret123", "role": "customer"})


def test_create_panel_user_customer_with_account_id(isolated_db, monkeypatch):
    from daemon import handlers_account as ha

    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    account = ha.create_account({"username": "demo1"})

    result = hauth.create_panel_user(
        {"username": "cust1", "password": "supersecret123", "role": "customer", "account_id": account["id"]}
    )
    assert result["account_id"] == account["id"]


def test_create_panel_user_rejects_short_password(isolated_db):
    with pytest.raises(ValidationError):
        hauth.create_panel_user({"username": "admin", "password": "short", "role": "admin"})


def test_create_panel_user_rejects_duplicate(isolated_db):
    hauth.create_panel_user({"username": "admin", "password": "supersecret123", "role": "admin"})
    with pytest.raises(RuntimeError):
        hauth.create_panel_user({"username": "admin", "password": "anotherpassword1", "role": "admin"})


def test_password_is_actually_hashed_and_verifiable(isolated_db):
    hauth.create_panel_user({"username": "admin", "password": "supersecret123", "role": "admin"})
    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import PanelUser

    with write_session() as session:
        user = session.scalar(select(PanelUser).where(PanelUser.username == "admin"))
        assert user.password_hash != "supersecret123"
        assert verify_password("supersecret123", user.password_hash)
        assert not verify_password("wrongpassword", user.password_hash)


def test_set_panel_user_password(isolated_db):
    hauth.create_panel_user({"username": "admin", "password": "supersecret123", "role": "admin"})
    hauth.set_panel_user_password({"username": "admin", "password": "newpassword456"})

    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import PanelUser

    with write_session() as session:
        user = session.scalar(select(PanelUser).where(PanelUser.username == "admin"))
        assert verify_password("newpassword456", user.password_hash)


def test_create_and_revoke_session(isolated_db):
    user = hauth.create_panel_user({"username": "admin", "password": "supersecret123", "role": "admin"})
    session_result = hauth.create_session({"panel_user_id": user["id"]})
    assert "session_id" in session_result

    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import Session as SessionModel

    with write_session() as db:
        row = db.scalar(select(SessionModel).where(SessionModel.session_id == session_result["session_id"]))
        assert row is not None
        assert row.revoked is False

    hauth.revoke_session({"session_id": session_result["session_id"]})
    with write_session() as db:
        row = db.scalar(select(SessionModel).where(SessionModel.session_id == session_result["session_id"]))
        assert row.revoked is True


def test_create_session_rejects_disabled_user(isolated_db):
    user = hauth.create_panel_user({"username": "admin", "password": "supersecret123", "role": "admin"})

    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import PanelUser

    with write_session() as db:
        row = db.get(PanelUser, user["id"])
        row.disabled = True

    with pytest.raises(RuntimeError):
        hauth.create_session({"panel_user_id": user["id"]})


def test_create_api_token_returns_raw_token_once(isolated_db):
    result = hauth.create_api_token({"label": "billing-system", "role": "admin"})
    assert result["token"].startswith("fh_admin_")

    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import ApiToken

    with write_session() as db:
        row = db.scalar(select(ApiToken).where(ApiToken.label == "billing-system"))
        assert row is not None
        assert row.token_hash != result["token"]  # never store the raw token


def test_revoke_api_token(isolated_db):
    result = hauth.create_api_token({"label": "billing-system", "role": "admin"})
    hauth.revoke_api_token({"token_id": result["id"]})

    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import ApiToken

    with write_session() as db:
        row = db.get(ApiToken, result["id"])
        assert row.revoked_at is not None
