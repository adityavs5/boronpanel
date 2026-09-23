import pytest

from daemon import handlers_auth as hauth
from shared.passwords import verify_password
from shared.validation import ValidationError


def test_create_panel_user_admin(isolated_db):
    result = hauth.create_panel_user({"username": "admin", "password": "SuperSecret123!", "role": "admin"})
    assert result["role"] == "admin"
    assert result["account_id"] is None


def test_create_panel_user_reseller_does_not_require_account(isolated_db):
    result = hauth.create_panel_user({"username": "seller", "password": "SuperSecret123!", "role": "reseller"})
    assert result["role"] == "reseller"
    assert result["account_id"] is None


def test_create_panel_user_customer_requires_account_id(isolated_db):
    with pytest.raises(ValidationError):
        hauth.create_panel_user({"username": "cust1", "password": "SuperSecret123!", "role": "customer"})


def test_create_panel_user_customer_with_account_id(isolated_db, monkeypatch):
    from daemon import handlers_account as ha

    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    account = ha.create_account({"username": "demo1"})

    result = hauth.create_panel_user(
        {"username": "cust1", "password": "SuperSecret123!", "role": "customer", "account_id": account["id"]}
    )
    assert result["account_id"] == account["id"]


def test_create_panel_user_rejects_short_password(isolated_db):
    with pytest.raises(ValidationError):
        hauth.create_panel_user({"username": "admin", "password": "short", "role": "admin"})


def test_create_panel_user_rejects_weak_password_missing_complexity(isolated_db):
    """Phase 4 feature 12: this codebase-wide audit's real finding -- panel
    login passwords (the most security-critical password in the whole
    system) previously only checked len(password) < 8, the weakest rule
    of any password path in the project. "supersecretpassword" (20 chars,
    all lowercase) would have passed the old check outright."""
    with pytest.raises(ValidationError):
        hauth.create_panel_user({"username": "admin", "password": "supersecretpassword", "role": "admin"})


def test_set_panel_user_password_rejects_weak_password(isolated_db):
    hauth.create_panel_user({"username": "admin", "password": "SuperSecret123!", "role": "admin"})
    with pytest.raises(ValidationError):
        hauth.set_panel_user_password({"username": "admin", "password": "alllowercase123"})


def test_create_panel_user_rejects_duplicate(isolated_db):
    hauth.create_panel_user({"username": "admin", "password": "SuperSecret123!", "role": "admin"})
    with pytest.raises(RuntimeError):
        hauth.create_panel_user({"username": "admin", "password": "AnotherPassword1!", "role": "admin"})


def test_password_is_actually_hashed_and_verifiable(isolated_db):
    hauth.create_panel_user({"username": "admin", "password": "SuperSecret123!", "role": "admin"})
    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import PanelUser

    with write_session() as session:
        user = session.scalar(select(PanelUser).where(PanelUser.username == "admin"))
        assert user.password_hash != "SuperSecret123!"
        assert verify_password("SuperSecret123!", user.password_hash)
        assert not verify_password("wrongpassword", user.password_hash)


def test_set_panel_user_password(isolated_db):
    hauth.create_panel_user({"username": "admin", "password": "SuperSecret123!", "role": "admin"})
    hauth.set_panel_user_password({"username": "admin", "password": "NewPassword456!"})

    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import PanelUser

    with write_session() as session:
        user = session.scalar(select(PanelUser).where(PanelUser.username == "admin"))
        assert verify_password("NewPassword456!", user.password_hash)


def test_create_and_revoke_session(isolated_db):
    from shared.session_ids import session_digest
    user = hauth.create_panel_user({"username": "admin", "password": "SuperSecret123!", "role": "admin"})
    session_result = hauth.create_session({"panel_user_id": user["id"]})
    assert "session_id" in session_result

    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import Session as SessionModel

    with write_session() as db:
        row = db.scalar(select(SessionModel).where(SessionModel.session_id == session_digest(session_result["session_id"])))
        assert row is not None
        assert row.session_id != session_result["session_id"]
        assert row.revoked is False

    hauth.revoke_session({"session_id": session_result["session_id"]})
    with write_session() as db:
        row = db.scalar(select(SessionModel).where(SessionModel.session_id == session_digest(session_result["session_id"])))
        assert row.revoked is True


def test_create_session_rejects_disabled_user(isolated_db):
    user = hauth.create_panel_user({"username": "admin", "password": "SuperSecret123!", "role": "admin"})

    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import PanelUser

    with write_session() as db:
        row = db.get(PanelUser, user["id"])
        row.disabled = True

    with pytest.raises(RuntimeError):
        hauth.create_session({"panel_user_id": user["id"]})


# --- Phase 7b feature 3: "new login to customer panel" notification --------


def _make_account(username="demo1"):
    from shared.db import write_session
    from shared.models import Account

    with write_session() as db:
        account = Account(username=username, status="active")
        db.add(account)
        db.flush()
        return account.id


def test_create_session_emits_login_event_for_customer(isolated_db, monkeypatch):
    account_id = _make_account()
    user = hauth.create_panel_user({"username": "cust1", "password": "SuperSecret123!", "role": "customer", "account_id": account_id})

    emitted = []
    monkeypatch.setattr(hauth.events, "emit", lambda event_type, account, **ctx: emitted.append((event_type, account.id)))
    hauth.create_session({"panel_user_id": user["id"]})

    assert emitted == [("login.new", account_id)]


def test_create_session_does_not_emit_login_event_for_admin(isolated_db, monkeypatch):
    user = hauth.create_panel_user({"username": "admin", "password": "SuperSecret123!", "role": "admin"})

    emitted = []
    monkeypatch.setattr(hauth.events, "emit", lambda *a, **k: emitted.append(a))
    hauth.create_session({"panel_user_id": user["id"]})

    assert emitted == []


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


# Security audit finding F11: password change revokes existing sessions.
def test_set_panel_user_password_revokes_existing_sessions(isolated_db):
    from shared.session_ids import session_digest
    user = hauth.create_panel_user({"username": "admin", "password": "SuperSecret123!", "role": "admin"})
    session_result = hauth.create_session({"panel_user_id": user["id"]})

    hauth.set_panel_user_password({"username": "admin", "password": "NewPassword456!"})

    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import Session as SessionModel

    with write_session() as db:
        row = db.scalar(select(SessionModel).where(SessionModel.session_id == session_digest(session_result["session_id"])))
        assert row.revoked is True


# Security audit finding F2: brute-force lockout on /login.
def test_check_login_lockout_unlocked_when_no_history(isolated_db):
    assert hauth.check_login_lockout({"username": "nobody"}) == {"locked": False}


def test_login_success_clears_reserved_failures(isolated_db):
    # check_login_lockout now RESERVES (counts) each attempt; a later success
    # clears the counter so earlier typos don't accumulate toward a lockout.
    for _ in range(3):
        assert hauth.check_login_lockout({"username": "admin"})["locked"] is False
    hauth.record_login_result({"username": "admin", "success": True})

    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import LoginAttempt

    with write_session() as db:
        row = db.scalar(select(LoginAttempt).where(LoginAttempt.username == "admin"))
        assert row is None


def test_reserving_attempts_locks_out_after_threshold(isolated_db):
    # The first THRESHOLD attempts proceed; the next one is refused atomically
    # (this is the gate that a concurrent burst can no longer slip past).
    for _ in range(hauth.LOCKOUT_THRESHOLD):
        assert hauth.check_login_lockout({"username": "admin"})["locked"] is False

    lockout = hauth.check_login_lockout({"username": "admin"})
    assert lockout["locked"] is True
    assert lockout["retry_after_seconds"] > 0


def test_below_threshold_does_not_lock(isolated_db):
    # THRESHOLD-1 reserved, then the THRESHOLD-th attempt still proceeds.
    for _ in range(hauth.LOCKOUT_THRESHOLD - 1):
        assert hauth.check_login_lockout({"username": "admin"})["locked"] is False
    assert hauth.check_login_lockout({"username": "admin"}) == {"locked": False}


def test_failed_result_does_not_double_count(isolated_db):
    # A failed attempt is already counted at reservation time, so
    # record_login_result(success=False) must be a no-op (no second increment).
    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import LoginAttempt

    hauth.check_login_lockout({"username": "admin"})  # reserve one attempt
    hauth.record_login_result({"username": "admin", "success": False})
    with write_session() as db:
        row = db.scalar(select(LoginAttempt).where(LoginAttempt.username == "admin"))
        assert row.failed_count == 1


def test_check_login_lockout_clears_after_expiry(isolated_db):
    import datetime as dt

    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import LoginAttempt

    for _ in range(hauth.LOCKOUT_THRESHOLD + 1):
        hauth.check_login_lockout({"username": "admin"})

    with write_session() as db:
        row = db.scalar(select(LoginAttempt).where(LoginAttempt.username == "admin"))
        assert row.locked_until is not None
        row.locked_until = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=1)

    # A fresh attempt after expiry starts a new window (reserved, not locked).
    assert hauth.check_login_lockout({"username": "admin"}) == {"locked": False}


@pytest.mark.parametrize('existing', [False, True])
def test_login_attempt_reservation_is_atomic_under_parallel_requests(isolated_db, existing):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    initial = 1 if existing else 0
    if existing:
        assert not hauth.check_login_lockout({'username': 'burst-test'})['locked']
    workers = hauth.LOCKOUT_THRESHOLD + 3
    barrier = Barrier(workers)
    def reserve(_):
        barrier.wait(timeout=10)
        return hauth.check_login_lockout({'username': 'burst-test'})
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(reserve, range(workers)))
    assert sum(not result['locked'] for result in results) == hauth.LOCKOUT_THRESHOLD - initial
