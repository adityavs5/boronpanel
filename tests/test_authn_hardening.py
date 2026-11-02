"""Regression tests for the "authn" security-review findings.

(a) API tokens are bound to the administrator who minted them.
(b) An anonymous client cannot keep a known login locked out.
(c) Login timing does not reveal which login names exist.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib

import pytest
from sqlalchemy import create_engine, select, text

from api import security as sec
from daemon import handlers_auth as hauth
from daemon import server
from daemon.rpc_authority import AuthenticationError, resolve_principal
from shared.db import write_session
from shared.models import Account, ApiToken, LoginAttempt, PanelUser, Session, utcnow
from shared.validation import ValidationError

OWNER_PASSWORD = "OwnerPassword123!"


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
    monkeypatch.setattr(server.audit, "record_account_event", lambda *args, **kwargs: None)


def _admin(username: str, password: str = OWNER_PASSWORD) -> int:
    return hauth.create_panel_user({"username": username, "password": password, "role": "admin"})["id"]


def _token(creator_id: int, **params) -> dict:
    return hauth.create_api_token({"label": "root-ops", "role": "admin", **params,
                                   "_creator_panel_user_id": creator_id})


def _proof(token: dict) -> dict:
    return {"type": "token", "value": token["token"]}


def _token_valid_everywhere(token: dict) -> bool:
    http = sec._identity_from_bearer_token(token["token"]) is not None
    try:
        resolve_principal(_proof(token))
        daemon = True
    except AuthenticationError:
        daemon = False
    assert http == daemon, "the API and daemon resolvers must agree"
    return daemon


def _revoked(token_id: int) -> bool:
    with write_session() as db:
        return db.get(ApiToken, token_id).revoked_at is not None


# --- (a) token creator binding ------------------------------------------------

def test_token_creation_requires_an_active_administrator_creator(isolated_db):
    with pytest.raises(ValidationError, match="active administrator"):
        hauth.create_api_token({"label": "x", "role": "admin"})
    disabled = _admin("gone-admin")
    with write_session() as db:
        db.get(PanelUser, disabled).disabled = True
    with pytest.raises(ValidationError, match="active administrator"):
        _token(disabled)
    with pytest.raises(ValidationError, match="active administrator"):
        hauth.create_api_token({"label": "x", "role": "admin", "_creator_panel_user_id": True})


def test_token_acts_and_is_audited_as_its_creator_not_its_label(isolated_db, monkeypatch):
    creator = _admin("mallory")
    _admin("root-ops")
    token = _token(creator, label="root-ops")
    assert token["created_by"] == "mallory"

    principal = resolve_principal(_proof(token))
    assert principal.username == "mallory"
    assert principal.token_id == token["id"]
    assert principal.token_creator_id == creator
    assert principal.panel_user_id is None  # session-only dispositions still refuse it
    identity = sec._identity_from_bearer_token(token["token"])
    assert identity.username == "mallory"
    assert identity.token_label == "root-ops"
    assert identity.panel_user_id == -1

    records = []
    monkeypatch.setattr(server.audit, "record", lambda *args, **kwargs: records.append(args))
    monkeypatch.setitem(server.OP_TABLE, "account.create", lambda _params: {"ok": True})  # audited op
    asyncio.run(server.dispatch("account.create", {}, _proof(token)))
    assert records and records[-1][0] == "mallory"


@pytest.mark.parametrize("change", ["disabled", "demoted", "deleted_row"])
def test_both_resolvers_refuse_token_once_creator_is_unusable(isolated_db, change):
    creator = _admin("leaver")
    _admin("keeper")
    token = _token(creator)
    assert _token_valid_everywhere(token)
    with write_session() as db:
        user = db.get(PanelUser, creator)
        if change == "disabled":
            user.disabled = True
        elif change == "demoted":
            user.role = "reseller"
        else:
            db.get(ApiToken, token["id"]).created_by_panel_user_id = None
    assert not _token_valid_everywhere(token)


def test_customer_tokens_follow_the_same_creator_rule(isolated_db):
    creator = _admin("leaver")
    _admin("keeper")
    with write_session() as db:
        account = Account(username="alice")
        db.add(account)
        db.flush()
        account_id = account.id
    token = _token(creator, role="customer", account_id=account_id)
    principal = resolve_principal(_proof(token))
    assert (principal.role, principal.account_id, principal.username) == ("customer", account_id, "leaver")
    hauth.set_administrator_status({"username": "leaver", "disabled": True, "actor_username": "keeper"})
    assert _revoked(token["id"])
    assert not _token_valid_everywhere(token)


def test_disabling_an_administrator_revokes_their_tokens_permanently(isolated_db):
    leaver = _admin("leaver")
    keeper = _admin("keeper")
    theirs, other = _token(leaver), _token(keeper)
    hauth.set_administrator_status({"username": "leaver", "disabled": True, "actor_username": "keeper"})
    assert _revoked(theirs["id"]) and not _revoked(other["id"])
    hauth.set_administrator_status({"username": "leaver", "disabled": False, "actor_username": "keeper"})
    assert not _token_valid_everywhere(theirs)
    assert _token_valid_everywhere(other)


@pytest.mark.parametrize("action", ["password", "reset_2fa", "delete"])
def test_administrator_management_revokes_tokens(isolated_db, action):
    leaver = _admin("leaver")
    _admin("keeper")
    token = _token(leaver)
    params = {"username": "leaver", "action": action, "actor_username": "keeper"}
    if action == "password":
        params["password"] = "ReplacedPassword123!"
    hauth.manage_administrator(params)
    with write_session() as db:
        row = db.get(ApiToken, token["id"])
        assert row.revoked_at is not None
        if action == "delete":
            assert db.get(PanelUser, leaver) is None
            assert row.created_by_panel_user_id is None
            assert row.label == "root-ops"  # still identifiable after detaching
    assert not _token_valid_everywhere(token)


def test_password_change_revokes_the_users_tokens(isolated_db):
    admin = _admin("owner")
    token = _token(admin)
    hauth.set_panel_user_password({"username": "owner", "password": "ReplacedPassword123!"})
    assert _revoked(token["id"])


def test_dispatch_binds_creator_from_credential_never_from_params(isolated_db, monkeypatch):
    monkeypatch.setattr(server.audit, "record", lambda *args, **kwargs: None)
    owner = _admin("owner")
    other = _admin("other")
    session_id = hauth.create_session({"panel_user_id": owner})["session_id"]
    created = asyncio.run(server.dispatch(
        "auth.create_api_token",
        {"label": "ci", "role": "admin", "_creator_panel_user_id": other},
        {"type": "session", "value": session_id},
    ))
    assert created["created_by"] == "owner"
    # A token-minted token inherits the original creator.
    child = asyncio.run(server.dispatch(
        "auth.create_api_token", {"label": "child", "role": "admin"}, _proof(created),
    ))
    assert child["created_by"] == "owner"
    with write_session() as db:
        assert db.get(ApiToken, child["id"]).created_by_panel_user_id == owner
    hauth.set_administrator_status({"username": "owner", "disabled": True, "actor_username": "other"})
    assert not _token_valid_everywhere(created)
    assert not _token_valid_everywhere(child)


def test_migration_revokes_existing_ownerless_tokens(tmp_path):
    from shared.migrations import apply_schema_migrations

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}", future=True)
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE panel_users (id INTEGER PRIMARY KEY, username VARCHAR(64))")
        conn.exec_driver_sql(
            "CREATE TABLE api_tokens (id INTEGER PRIMARY KEY, token_hash VARCHAR(64), "
            "label VARCHAR(128), role VARCHAR(16), account_id INTEGER, created_at DATETIME, "
            "revoked_at DATETIME, scope VARCHAR(16) NOT NULL DEFAULT 'full', expires_at DATETIME)"
        )
        conn.exec_driver_sql(
            "INSERT INTO api_tokens (id, token_hash, label, role, created_at) VALUES "
            "(1, 'a', 'legacy-admin', 'admin', '2026-10-01 00:00:00.000000'), "
            "(2, 'b', 'legacy-customer', 'customer', '2026-10-01 00:00:00.000000')"
        )
        conn.exec_driver_sql(
            "INSERT INTO api_tokens (id, token_hash, label, role, created_at, revoked_at) VALUES "
            "(3, 'c', 'already-revoked', 'admin', '2026-10-01 00:00:00.000000', "
            "'2026-10-02 00:00:00.000000')"
        )
    apply_schema_migrations(engine)
    apply_schema_migrations(engine)  # idempotent
    with engine.connect() as conn:
        columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(api_tokens)")}
        rows = dict(conn.execute(text("SELECT id, revoked_at FROM api_tokens")).all())
    assert "created_by_panel_user_id" in columns
    assert rows[1] and rows[2]
    assert rows[3] == "2026-10-02 00:00:00.000000"  # history untouched


def test_resolvers_refuse_preexisting_ownerless_rows(isolated_db):
    raw = "fh_admin_ownerless-row"
    with write_session() as db:
        db.add(ApiToken(token_hash=hashlib.sha256(raw.encode()).hexdigest(), label="old", role="admin"))
    assert sec._identity_from_bearer_token(raw) is None
    with pytest.raises(AuthenticationError):
        resolve_principal({"type": "token", "value": raw})


# --- (b) lockout cannot be weaponised against the real owner ---------------

def _login(password: str, ip: str, username: str = "admin") -> dict:
    return hauth.login_begin({"username": username, "password": password, "source_ip": ip})


def test_attacker_ip_lockout_does_not_block_owner_from_another_ip(isolated_db):
    _admin("admin")
    for _ in range(hauth.LOCKOUT_THRESHOLD):
        assert _login("wrong", "198.51.100.7") == {"valid": False}
    assert _login(OWNER_PASSWORD, "198.51.100.7")["locked"] is True  # attacker's own source
    assert _login(OWNER_PASSWORD, "203.0.113.20")["valid"] is True  # owner unaffected


def test_ipv6_sources_are_grouped_by_64(isolated_db):
    _admin("admin")
    for index in range(hauth.LOCKOUT_THRESHOLD):
        _login("wrong", f"2001:db8:1:2::{index + 1}")
    assert _login(OWNER_PASSWORD, "2001:db8:1:2::ffff")["locked"] is True
    assert _login(OWNER_PASSWORD, "2001:db8:1:3::1")["valid"] is True


def test_distributed_guessing_is_stopped_but_trusted_owner_source_still_signs_in(isolated_db):
    owner = _admin("admin")
    # The owner signed in from their usual address before the campaign.
    assert _login(OWNER_PASSWORD, "203.0.113.20")["valid"] is True
    for index in range(hauth.GLOBAL_LOCKOUT_THRESHOLD + 1):
        result = _login("wrong", f"10.0.{index // 200}.{index % 200 + 1}")
    assert result["locked"] is True  # the global guard tripped
    # A fresh source is refused even with the right password, before bcrypt.
    assert _login(OWNER_PASSWORD, "192.0.2.99")["locked"] is True
    # The owner's known source is never refused by the global guard.
    assert _login(OWNER_PASSWORD, "203.0.113.20")["valid"] is True
    with write_session() as db:
        assert db.scalar(select(Session.id).where(
            Session.panel_user_id == owner, Session.ip_address == "203.0.113.20",
        ).order_by(Session.id.desc())) is not None


def test_global_guard_expires_and_resets_after_quiet_period(isolated_db):
    _admin("admin")
    for index in range(hauth.GLOBAL_LOCKOUT_THRESHOLD + 1):
        _login("wrong", f"10.1.0.{index + 1}")
    assert _login(OWNER_PASSWORD, "192.0.2.99")["locked"] is True
    bucket = hauth._login_bucket("admin", known_user=True)
    with write_session() as db:
        row = db.scalar(select(LoginAttempt).where(LoginAttempt.username == bucket))
        row.locked_until = utcnow() - dt.timedelta(seconds=1)
    assert _login(OWNER_PASSWORD, "192.0.2.99")["valid"] is True


def test_second_factor_step_is_gated_per_source(isolated_db):
    import pyotp

    from daemon import totp

    owner = _admin("admin")
    setup = totp.setup_totp({"panel_user_id": owner})
    totp.verify_totp({"panel_user_id": owner, "code": pyotp.TOTP(setup["secret"]).now()})
    for _ in range(hauth.LOCKOUT_THRESHOLD + 1):
        _login("wrong", "198.51.100.7")
    begun = _login(OWNER_PASSWORD, "203.0.113.20")
    assert begun["needs_2fa"] is True
    code = pyotp.TOTP(setup["secret"]).at(totp.time.time() + 30)
    finished = hauth.login_finish({"pending_token": begun["pending_token"], "code": code,
                                   "source_ip": "203.0.113.20"})
    assert finished["valid"] is True


def test_idle_source_rows_are_pruned(isolated_db):
    _admin("admin")
    _login("wrong", "198.51.100.7")
    with write_session() as db:
        rows = db.scalars(select(LoginAttempt).where(
            LoginAttempt.username.like(f"{hauth.SOURCE_KEY_PREFIX}%"))).all()
        assert len(rows) == 1
        rows[0].updated_at = utcnow() - dt.timedelta(seconds=hauth.SOURCE_ATTEMPT_RETENTION_SECONDS + 5)
    hauth.reconcile_login_attempt_keys()
    with write_session() as db:
        assert db.scalar(select(LoginAttempt).where(
            LoginAttempt.username.like(f"{hauth.SOURCE_KEY_PREFIX}%"))) is None


def test_unknown_names_lock_the_same_way_as_real_ones(isolated_db):
    _admin("admin")
    for name in ("admin", "nobody-here"):
        for _ in range(hauth.LOCKOUT_THRESHOLD):
            assert _login("wrong", "198.51.100.7", name) == {"valid": False}
        assert _login("wrong", "198.51.100.7", name)["locked"] is True
        assert _login("wrong", "198.51.100.8", name) == {"valid": False}


# --- (c) no username enumeration by timing ---------------------------------

@pytest.mark.parametrize("state", ["unknown", "disabled", "enabled"])
def test_every_login_attempt_spends_one_bcrypt_check(isolated_db, monkeypatch, state):
    admin = _admin("admin")
    if state == "disabled":
        _admin("other")
        with write_session() as db:
            db.get(PanelUser, admin).disabled = True
    calls = []
    real_verify = hauth.verify_password
    monkeypatch.setattr(hauth, "verify_password",
                        lambda password, hashed: calls.append(hashed) or real_verify(password, hashed))
    name = "ghost" if state == "unknown" else "admin"
    assert _login("wrong-password", "198.51.100.7", name) == {"valid": False}
    assert len(calls) == 1
    if state == "enabled":
        assert calls[0] != hauth._dummy_password_hash()
    else:
        assert calls[0] == hauth._dummy_password_hash()
        assert calls[0].startswith("$2")  # a real bcrypt hash, same work factor family


def test_break_glass_2fa_reset_revokes_the_administrators_tokens(isolated_db, monkeypatch):
    from scripts import reset_admin_2fa

    owner = _admin("owner")
    keeper = _admin("keeper")
    theirs, other = _token(owner), _token(keeper)
    monkeypatch.setattr(reset_admin_2fa, "init_db", lambda: None)
    monkeypatch.setattr(reset_admin_2fa.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(reset_admin_2fa.sys, "argv", ["reset_admin_2fa.py", "owner", "--confirm"])
    assert reset_admin_2fa.main() == 0
    assert _revoked(theirs["id"]) and not _revoked(other["id"])
    assert not _token_valid_everywhere(theirs)
