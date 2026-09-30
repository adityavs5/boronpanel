import datetime as dt

import pytest
from sqlalchemy import select

from daemon import handlers_auth
from shared.db import write_session
from shared.models import PanelUser, Session
from shared.validation import ValidationError


def test_administrator_password_is_returned_once_and_not_listed(isolated_db):
    created = handlers_auth.create_administrator({"username": "operator", "password": "StrongOperatorPass123!"})
    assert created["initial_password"] == "StrongOperatorPass123!"
    listed = handlers_auth.list_administrators({})["administrators"]
    assert len(listed) == 1
    assert {key: listed[0][key] for key in ("id", "username", "role", "account_id", "disabled")} == {
        key: created[key] for key in ("id", "username", "role", "account_id", "disabled")
    }
    assert listed[0]["created_at"]
    assert "password_hash" not in listed[0] and "initial_password" not in listed[0]


def test_disabling_administrator_revokes_sessions_and_protects_current_login(isolated_db):
    first = handlers_auth.create_administrator({"username": "primary", "password": "StrongPrimaryPass123!"})
    second = handlers_auth.create_administrator({"username": "operator", "password": "StrongOperatorPass123!"})
    with write_session() as db:
        db.add(Session(session_id="a" * 64, panel_user_id=second["id"],
                       expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)))
    result = handlers_auth.set_administrator_status({"username": "operator", "actor_username": "primary", "disabled": True})
    assert result["disabled"] is True
    with write_session() as db:
        assert db.scalar(select(Session.revoked).where(Session.panel_user_id == second["id"])) is True
    with pytest.raises(ValidationError, match="your own"):
        handlers_auth.set_administrator_status({"username": "primary", "actor_username": "primary", "disabled": True})


def test_administrator_removal_revokes_credentials_and_protects_self(isolated_db):
    from shared.models import LoginChallenge, TotpCredential, TotpRecoveryCode, utcnow
    from shared.session_ids import session_digest
    primary = handlers_auth.create_administrator({'username':'primary','password':'StrongPrimaryPass123!'})
    operator = handlers_auth.create_administrator({'username':'operator','password':'StrongOperatorPass123!'})
    proof = handlers_auth.create_session({'panel_user_id':operator['id']})
    with write_session() as db:
        db.add(TotpCredential(panel_user_id=operator['id'],secret='test-only',enabled=True))
        db.add(TotpRecoveryCode(panel_user_id=operator['id'],code_hash='a'*64))
        db.add(LoginChallenge(panel_user_id=operator['id'],token_hash='b'*64,password_hash='test-only',expires_at=utcnow()+dt.timedelta(minutes=1)))
    with pytest.raises(ValidationError,match='your own'):
        handlers_auth.manage_administrator({'username':'primary','actor_username':'primary','action':'delete'})
    handlers_auth.manage_administrator({'username':'operator','actor_username':'primary','action':'reset_2fa'})
    with write_session() as db:
        assert db.scalar(select(Session.revoked).where(Session.session_id == session_digest(proof['session_id'])))
        assert db.scalar(select(TotpCredential).where(TotpCredential.panel_user_id==operator['id'])) is None
        assert db.scalar(select(LoginChallenge).where(LoginChallenge.panel_user_id==operator['id'])) is None
    handlers_auth.manage_administrator({'username':'operator','actor_username':'primary','action':'delete'})
    with write_session() as db:
        assert db.get(PanelUser, operator['id']) is None
        assert db.scalar(select(Session).where(Session.panel_user_id==operator['id'])) is None
    with pytest.raises(ValidationError,match='remain enabled'):
        handlers_auth.manage_administrator({'username':'primary','actor_username':'external','action':'delete'})
