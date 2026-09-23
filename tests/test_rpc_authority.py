"""The API UID and caller-supplied labels never grant root RPC authority."""
import asyncio
import datetime as dt

import pytest
from sqlalchemy import select

from daemon import handlers_auth, server
from daemon.rpc_authority import AuthenticationError, AuthorizationError
from daemon.rpc_policy import POLICY_BY_OPERATION
from shared.db import write_session
from shared.models import Account, ApiToken, BackupDestination, BackupJob, PanelUser, RestoreJob, Session, utcnow
from shared.session_ids import session_digest


@pytest.fixture(autouse=True)
def immediate_handlers(monkeypatch):
    def run_immediately(loop, _executor, handler, params):
        result = loop.create_future()
        try:
            result.set_result(handler(params))
        except Exception as exc:
            result.set_exception(exc)
        return result

    monkeypatch.setattr(asyncio.BaseEventLoop, "run_in_executor", run_immediately)
    monkeypatch.setattr(server.audit, "record", lambda *args, **kwargs: None)
    monkeypatch.setattr(server.audit, "record_account_event", lambda *args, **kwargs: None)


def _setup_users():
    with write_session() as db:
        db.add_all([Account(username="alice"), Account(username="bob")])
    with write_session() as db:
        alice = db.scalar(select(Account).where(Account.username == "alice"))
    admin = handlers_auth.create_panel_user({
        "username": "administrator", "password": "AdminPassword123!", "role": "admin",
    })
    customer = handlers_auth.create_panel_user({
        "username": "alice-login", "password": "CustomerPass123!", "role": "customer",
        "account_id": alice.id,
    })
    admin_session = handlers_auth.create_session({"panel_user_id": admin["id"]})["session_id"]
    customer_session = handlers_auth.create_session({"panel_user_id": customer["id"]})["session_id"]
    return admin_session, customer_session


def _credential(raw):
    return {"type": "session", "value": raw}


def test_forged_metadata_without_credential_never_enters_handler(isolated_db, monkeypatch):
    called = []
    monkeypatch.setitem(server.OP_TABLE, "account.create", lambda _: called.append(True))
    with pytest.raises(AuthenticationError):
        asyncio.run(server.dispatch("account.create", {"_actor": "administrator", "_role": "admin", "username": "x"}))
    assert called == []


def test_valid_admin_works_and_client_labels_are_ignored(isolated_db, monkeypatch):
    admin_session, _ = _setup_users()
    seen = []
    monkeypatch.setitem(server.OP_TABLE, "account.create", lambda params: seen.append(params) or {"ok": True})
    assert asyncio.run(server.dispatch("account.create", {"username": "newacct", "_actor": "fake", "_role": "customer"},
                                       _credential(admin_session))) == {"ok": True}
    assert seen == [{"username": "newacct"}]


def test_customer_account_scope_checked_in_root_db(isolated_db, monkeypatch):
    _, customer_session = _setup_users()
    seen = []
    monkeypatch.setitem(server.OP_TABLE, "account.get", lambda params: seen.append(params["username"]) or {"ok": True})
    assert asyncio.run(server.dispatch("account.get", {"username": "alice"}, _credential(customer_session))) == {"ok": True}
    with pytest.raises(AuthorizationError):
        asyncio.run(server.dispatch("account.get", {"username": "bob", "_role": "admin"},
                                    _credential(customer_session)))
    assert seen == ["alice"]


def test_revoked_and_expired_sessions_rejected_before_handler(isolated_db, monkeypatch):
    admin_session, _ = _setup_users()
    seen = []
    monkeypatch.setitem(server.OP_TABLE, "account.create", lambda _: seen.append(True))
    with write_session() as db:
        row = db.scalar(select(Session).where(Session.session_id == session_digest(admin_session)))
        row.revoked = True
    with pytest.raises(AuthenticationError):
        asyncio.run(server.dispatch("account.create", {}, _credential(admin_session)))
    with write_session() as db:
        row = db.scalar(select(Session).where(Session.session_id == session_digest(admin_session)))
        row.revoked = False
        row.expires_at = utcnow() - dt.timedelta(seconds=1)
    with pytest.raises(AuthenticationError):
        asyncio.run(server.dispatch("account.create", {}, _credential(admin_session)))
    assert seen == []


def test_revoked_and_expired_api_tokens_rejected(isolated_db, monkeypatch):
    token = handlers_auth.create_api_token({"label": "integration", "role": "admin"})
    seen = []
    monkeypatch.setitem(server.OP_TABLE, "account.create", lambda _: seen.append(True) or {"ok": True})
    proof = {"type": "token", "value": token["token"]}
    assert asyncio.run(server.dispatch("account.create", {}, proof)) == {"ok": True}
    with write_session() as db:
        row = db.get(ApiToken, token["id"])
        row.revoked_at = utcnow()
    with pytest.raises(AuthenticationError):
        asyncio.run(server.dispatch("account.create", {}, proof))
    with write_session() as db:
        row = db.get(ApiToken, token["id"])
        row.revoked_at = None
        row.created_at = utcnow() - dt.timedelta(days=91)
    with pytest.raises(AuthenticationError):
        asyncio.run(server.dispatch("account.create", {}, proof))
    assert len(seen) == 1


def test_registry_policy_exact_equality_and_internal_ops_blocked(isolated_db):
    assert set(server.OP_TABLE) == set(POLICY_BY_OPERATION)
    admin_session, _ = _setup_users()
    with pytest.raises(AuthorizationError):
        asyncio.run(server.dispatch("panel_user.create", {}, _credential(admin_session)))
    with pytest.raises(AuthenticationError):
        asyncio.run(server.dispatch("auth.create_session", {"panel_user_id": 1}))


def test_restore_job_policy_resolves_row_owner(isolated_db, monkeypatch):
    admin_session, alice_session = _setup_users()
    with write_session() as db:
        bob = db.scalar(select(Account).where(Account.username == "bob"))
        destination = BackupDestination(name="local", kind="local", local_path="/tmp/backups")
        db.add(destination)
        db.flush()
        backup = BackupJob(account_id=bob.id, kind="full", destination_id=destination.id)
        db.add(backup)
        db.flush()
        restore = RestoreJob(backup_job_id=backup.id, account_id=bob.id, kind="full")
        db.add(restore)
        db.flush()
        restore_id = restore.id
    entered = []
    monkeypatch.setitem(server.OP_TABLE, "backup.restore.get", lambda _: entered.append(True) or {"ok": True})
    with pytest.raises(AuthorizationError):
        asyncio.run(server.dispatch("backup.restore.get", {"restore_job_id": restore_id, "username": "alice"},
                                    _credential(alice_session)))
    with pytest.raises(AuthorizationError):
        asyncio.run(server.dispatch("backup.restore.get", {"restore_job_id": restore_id, "username": "alice"},
                                    _credential(admin_session)))
    assert asyncio.run(server.dispatch("backup.restore.get", {"restore_job_id": restore_id, "username": "bob"},
                                       _credential(admin_session))) == {"ok": True}
    assert entered == [True]


def test_root_login_requires_password_before_session_mint(isolated_db):
    handlers_auth.create_panel_user({
        "username": "administrator", "password": "AdminPassword123!", "role": "admin",
    })
    bad = asyncio.run(server.dispatch("auth.login.begin", {
        "username": "administrator", "password": "incorrect",
    }))
    assert bad == {"valid": False}
    with write_session() as db:
        assert db.scalar(select(Session)) is None
    good = asyncio.run(server.dispatch("auth.login.begin", {
        "username": "administrator", "password": "AdminPassword123!",
    }))
    assert good["valid"] is True
    assert good["session_id"]
    with write_session() as db:
        row = db.scalar(select(Session))
        assert row.session_id == session_digest(good["session_id"])


def test_root_second_factor_challenge_is_one_use_and_bound_to_password(isolated_db):
    import pyotp

    from daemon import totp

    user = handlers_auth.create_panel_user({
        "username": "administrator", "password": "AdminPassword123!", "role": "admin",
    })
    setup = totp.setup_totp({"panel_user_id": user["id"]})
    totp.verify_totp({"panel_user_id": user["id"], "code": pyotp.TOTP(setup["secret"]).now()})
    begin = asyncio.run(server.dispatch("auth.login.begin", {
        "username": "administrator", "password": "AdminPassword123!",
    }))
    assert begin["needs_2fa"] is True
    assert "session_id" not in begin
    proof = {"pending_token": begin["pending_token"], "code": pyotp.TOTP(setup["secret"]).now()}
    finished = asyncio.run(server.dispatch("auth.login.finish", proof))
    assert finished["valid"] is True
    assert asyncio.run(server.dispatch("auth.login.finish", proof)) == {"valid": False}

    another = asyncio.run(server.dispatch("auth.login.begin", {
        "username": "administrator", "password": "AdminPassword123!",
    }))
    handlers_auth.set_panel_user_password({"username": "administrator", "password": "NewPassword456!"})
    assert asyncio.run(server.dispatch("auth.login.finish", {
        "pending_token": another["pending_token"], "code": pyotp.TOTP(setup["secret"]).now(),
    })) == {"valid": False}


def test_customer_password_change_requires_root_verified_current_password(isolated_db, monkeypatch):
    _, customer_session = _setup_users()
    entered = []
    monkeypatch.setitem(server.OP_TABLE, "panel_user.set_password", lambda _: entered.append(True) or {"ok": True})
    params = {"username": "alice-login", "password": "NewPassword456!"}
    for proof in (None, "incorrect"):
        attempt = {**params, **({"current_password": proof} if proof is not None else {})}
        with pytest.raises(AuthorizationError):
            asyncio.run(server.dispatch("panel_user.set_password", attempt, _credential(customer_session)))
    assert asyncio.run(server.dispatch("panel_user.set_password", {
        **params, "current_password": "CustomerPass123!",
    }, _credential(customer_session))) == {"ok": True}
    assert entered == [True]


def test_totp_disable_requires_root_verified_current_password(isolated_db, monkeypatch):
    _, customer_session = _setup_users()
    with write_session() as db:
        panel_user_id = db.scalar(select(PanelUser.id).where(PanelUser.username == "alice-login"))
    entered = []
    monkeypatch.setitem(server.OP_TABLE, "totp.disable", lambda _: entered.append(True) or {"ok": True})
    params = {"panel_user_id": panel_user_id}
    for proof in (None, "incorrect"):
        attempt = {**params, **({"current_password": proof} if proof is not None else {})}
        with pytest.raises(AuthorizationError):
            asyncio.run(server.dispatch("totp.disable", attempt, _credential(customer_session)))
    assert asyncio.run(server.dispatch("totp.disable", {
        **params, "current_password": "CustomerPass123!",
    }, _credential(customer_session))) == {"ok": True}
    assert entered == [True]


def test_customer_destination_list_is_bound_to_account(isolated_db, monkeypatch):
    _, customer_session = _setup_users()
    entered = []
    monkeypatch.setitem(server.OP_TABLE, "backup.destination.list", lambda _: entered.append(True) or {"destinations": []})
    with pytest.raises(AuthorizationError):
        asyncio.run(server.dispatch("backup.destination.list", {}, _credential(customer_session)))
    with pytest.raises(AuthorizationError):
        asyncio.run(server.dispatch("backup.destination.list", {"username": "bob"}, _credential(customer_session)))
    assert asyncio.run(server.dispatch("backup.destination.list", {"username": "alice"},
                                       _credential(customer_session))) == {"destinations": []}
    assert entered == [True]


def test_update_requires_root_confirmed_admin_session_and_second_factor(isolated_db, monkeypatch):
    import pyotp

    from daemon import totp

    admin_session, _ = _setup_users()
    entered = []
    monkeypatch.setitem(server.OP_TABLE, "update.start", lambda _: entered.append(True) or {"ok": True})
    base = {"initiated_by": "administrator"}
    with pytest.raises(AuthorizationError):
        asyncio.run(server.dispatch("update.start", base, _credential(admin_session)))
    admin_token = handlers_auth.create_api_token({"label": "automation", "role": "admin"})["token"]
    with pytest.raises(AuthorizationError):
        asyncio.run(server.dispatch("update.start", {**base, "confirm": True},
                                    {"type": "token", "value": admin_token}))
    with write_session() as db:
        admin_id = db.scalar(select(PanelUser.id).where(PanelUser.username == "administrator"))
    setup = totp.setup_totp({"panel_user_id": admin_id})
    code = pyotp.TOTP(setup["secret"]).now()
    totp.verify_totp({"panel_user_id": admin_id, "code": code})
    with pytest.raises(AuthorizationError):
        asyncio.run(server.dispatch("update.start", {**base, "confirm": True}, _credential(admin_session)))
    with pytest.raises(AuthorizationError):
        asyncio.run(server.dispatch("update.start", {**base, "confirm": True, "totp_code": "00000000"},
                                    _credential(admin_session)))
    assert asyncio.run(server.dispatch("update.start", {**base, "confirm": True, "totp_code": code},
                                       _credential(admin_session))) == {"ok": True}
    assert entered == [True]
