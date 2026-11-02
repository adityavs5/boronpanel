"""A suspended or terminated hosting account is refused at sign-in, with the
reason and without a session (live finding UW L10: the login "succeeded"
with a cookie, then every call answered 401 with no explanation), and an
impersonation session ends with the account's usability."""
from __future__ import annotations

import pyotp
import pytest
from sqlalchemy import func, select

from daemon import handlers_auth as hauth
from daemon import impersonation, totp
from daemon.rpc_authority import AuthenticationError, resolve_principal
from shared.db import write_session
from shared.models import Account, LoginChallenge, PanelUser, Session

PASSWORD = "CustomerPass123!"
SUSPENDED = "This hosting account is suspended. Contact your hosting provider."
GONE = "This hosting account is no longer available."


def _customer(status: str = "active", username: str = "cust1") -> tuple[int, int]:
    with write_session() as db:
        account = Account(username=username, status=status)
        db.add(account)
        db.flush()
        account_id = account.id
    user = hauth.create_panel_user({"username": username, "password": PASSWORD, "role": "customer",
                                    "account_id": account_id})
    return account_id, user["id"]


def _set_status(account_id: int, status: str, *, disable_logins: bool = False) -> None:
    with write_session() as db:
        db.get(Account, account_id).status = status
        if disable_logins:  # what termination does to the account's logins
            for user in db.scalars(select(PanelUser).where(PanelUser.account_id == account_id)).all():
                user.disabled = True


def _session_count(user_id: int) -> int:
    with write_session() as db:
        return db.scalar(select(func.count()).select_from(Session).where(Session.panel_user_id == user_id))


def _login(password: str = PASSWORD, username: str = "cust1", ip: str = "203.0.113.20") -> dict:
    return hauth.login_begin({"username": username, "password": password, "source_ip": ip})


def _enable_totp(user_id: int) -> str:
    secret = totp.setup_totp({"panel_user_id": user_id})["secret"]
    totp.verify_totp({"panel_user_id": user_id, "code": pyotp.TOTP(secret).now()})
    return secret


def test_suspended_customer_is_told_and_gets_no_session(isolated_db):
    account_id, user_id = _customer()
    _set_status(account_id, "suspended")
    assert _login() == {"valid": False, "account_unavailable": SUSPENDED}
    assert _session_count(user_id) == 0
    # Without the password nothing about the account is revealed.
    assert _login("WrongPassword123!") == {"valid": False}


def test_terminated_customer_is_told_the_account_is_gone(isolated_db):
    account_id, user_id = _customer()
    _set_status(account_id, "terminated", disable_logins=True)
    assert _login() == {"valid": False, "account_unavailable": GONE}
    assert _login("WrongPassword123!") == {"valid": False}
    assert _session_count(user_id) == 0


def test_other_disabled_logins_still_look_unknown(isolated_db):
    admin = hauth.create_panel_user({"username": "admin", "password": PASSWORD, "role": "admin"})
    with write_session() as db:
        db.get(PanelUser, admin["id"]).disabled = True
    assert _login(username="admin") == {"valid": False}
    # A disabled customer login of an account that is still active as well.
    account_id, _ = _customer()
    _set_status(account_id, "active", disable_logins=True)
    assert _login() == {"valid": False}


def test_refusal_comes_before_any_second_factor_challenge(isolated_db):
    account_id, user_id = _customer()
    _enable_totp(user_id)
    _set_status(account_id, "suspended")
    assert _login() == {"valid": False, "account_unavailable": SUSPENDED}
    with write_session() as db:
        assert db.scalar(select(LoginChallenge.id)) is None


def test_second_factor_completion_checks_the_account_again(isolated_db):
    account_id, user_id = _customer()
    secret = _enable_totp(user_id)
    begun = _login()
    assert begun["needs_2fa"] is True
    _set_status(account_id, "suspended")  # suspended during the 2FA step
    code = pyotp.TOTP(secret).at(totp.time.time() + 30)
    finished = hauth.login_finish({"pending_token": begun["pending_token"], "code": code,
                                   "source_ip": "203.0.113.20"})
    assert finished == {"valid": False, "account_unavailable": SUSPENDED}
    assert _session_count(user_id) == 0
    with write_session() as db:
        assert db.scalar(select(LoginChallenge)).used_at is not None  # not replayable


def test_termination_during_the_second_factor_step_is_reported(isolated_db):
    account_id, user_id = _customer()
    secret = _enable_totp(user_id)
    begun = _login()
    _set_status(account_id, "terminated", disable_logins=True)
    code = pyotp.TOTP(secret).at(totp.time.time() + 30)
    pending = {"pending_token": begun["pending_token"], "source_ip": "203.0.113.20"}
    # A wrong code still learns nothing.
    assert hauth.login_finish({**pending, "code": "000000" if code != "000000" else "111111"}) == {"valid": False}
    assert hauth.login_finish({**pending, "code": code}) == {"valid": False, "account_unavailable": GONE}
    assert _session_count(user_id) == 0


def test_refused_logins_do_not_lock_the_owner_out(isolated_db):
    account_id, user_id = _customer()
    _set_status(account_id, "suspended")
    for _ in range(hauth.LOCKOUT_THRESHOLD * 2):
        assert _login()["account_unavailable"] == SUSPENDED
    _set_status(account_id, "active")  # unsuspended
    result = _login()
    assert result["valid"] is True and result["session_id"]
    assert _session_count(user_id) == 1


@pytest.mark.parametrize("route,form", [
    ("/login", {"username": "cust1", "password": PASSWORD}),
    ("/login/2fa", {"pending_token": "pending", "code": "123456"}),
])
def test_login_routes_answer_403_with_the_reason_and_set_no_cookie(isolated_db, monkeypatch, route, form):
    import warnings

    warnings.filterwarnings("ignore")
    from testsupport.sync_asgi import SyncASGIClient as TestClient

    import api.main as main
    from api import ratelimit
    from api.routers import auth as auth_router

    monkeypatch.setattr(ratelimit, "limiter", ratelimit.SlidingWindowLimiter())
    monkeypatch.setattr(auth_router, "call_daemon_anonymous",
                        lambda op, **_params: {"valid": False, "account_unavailable": SUSPENDED})
    response = TestClient(main.app, follow_redirects=False).post(route, data=form)
    assert response.status_code == 403
    body = response.json()
    assert body["detail"] == SUSPENDED and body["account_unavailable"] is True
    assert "set-cookie" not in response.headers


def test_refusals_do_not_use_up_the_api_sign_in_budget(isolated_db, monkeypatch):
    """Round 2 review: the daemon refunds a refused login (the password was
    right), but the API's per-(IP, login) limiter still counted it, so a
    customer who retried got "Too many requests." instead of the reason."""
    import warnings

    warnings.filterwarnings("ignore")
    from testsupport.sync_asgi import SyncASGIClient as TestClient

    import api.main as main
    from api import ratelimit
    from api.routers import auth as auth_router

    monkeypatch.setattr(ratelimit, "limiter", ratelimit.SlidingWindowLimiter())
    daemon_answer = {"valid": False, "account_unavailable": SUSPENDED}
    monkeypatch.setattr(auth_router, "call_daemon_anonymous", lambda op, **_params: dict(daemon_answer))
    client = TestClient(main.app, follow_redirects=False)
    form = {"username": "cust1", "password": PASSWORD}
    attempts = ratelimit.LOGIN_LIMIT[0] + 2
    assert [client.post("/login", data=form).status_code for _ in range(attempts)] == [403] * attempts
    assert [client.post("/login/2fa", data={"pending_token": "p", "code": "123456"}).status_code
            for _ in range(attempts)] == [403] * attempts
    # Wrong passwords are still limited.
    daemon_answer = {"valid": False}
    statuses = [client.post("/login", data=form).status_code for _ in range(ratelimit.LOGIN_LIMIT[0] + 1)]
    assert statuses[:-1] == [401] * ratelimit.LOGIN_LIMIT[0] and statuses[-1] == 429


@pytest.mark.parametrize("status,usable", [
    ("active", True), ("suspended", True),
    ("terminating", False), ("terminated", False), ("error", False),
])
def test_impersonation_session_follows_the_account_status(isolated_db, status, usable):
    """Second layer behind termination's end_account_sessions: both
    resolvers refuse an impersonation of an account that is no longer
    active or suspended, as redeem_token does."""
    from api import security

    account_id, _ = _customer()
    admin = hauth.create_panel_user({"username": "admin", "password": "AdminPassword123!", "role": "admin"})
    token = impersonation.create_token({"username": "cust1", "admin_username": "admin"})["token"]
    redeemed = impersonation.redeem_token({"token": token, "admin_username": "admin",
                                           "admin_panel_user_id": admin["id"], "admin_session_id": "s"})
    _set_status(account_id, status)  # without end_account_sessions running

    identity = security._identity_from_session_cookie(security.sign_session_id(redeemed["session_id"]))
    credential = {"type": "session", "value": redeemed["session_id"]}
    if usable:
        assert identity is not None and identity.is_impersonating
        assert resolve_principal(credential).impersonating is True
    else:
        assert identity is None
        with pytest.raises(AuthenticationError, match="impersonation"):
            resolve_principal(credential)


def test_account_in_any_other_status_is_refused_with_a_reason(isolated_db):
    """Round 2 cross-group: the resolvers refuse every non-active status, so
    a status customer_login_refusal does not name must not get a session."""
    account_id, user_id = _customer()
    _set_status(account_id, "renaming")
    result = _login()
    assert result["valid"] is False and "not available right now" in result["account_unavailable"]
    assert _session_count(user_id) == 0


def _reseller(status: str) -> int:
    from shared.models import ResellerPlan, ResellerProfile

    user = hauth.create_panel_user({"username": "resel1", "password": PASSWORD, "role": "reseller"})
    with write_session() as db:
        plan = ResellerPlan(name="starter")
        db.add(plan)
        db.flush()
        db.add(ResellerProfile(panel_user_id=user["id"], plan_id=plan.id, status=status))
        db.get(PanelUser, user["id"]).disabled = status != "active"  # as reseller.update does
    return user["id"]


def test_suspended_reseller_is_told_and_gets_no_session(isolated_db):
    user_id = _reseller("suspended")
    assert _login(username="resel1") == {
        "valid": False, "account_unavailable": "This reseller login is suspended. Contact your hosting provider."}
    assert _login("WrongPassword123!", username="resel1") == {"valid": False}
    assert _session_count(user_id) == 0


def test_reseller_disabled_without_suspension_still_looks_unknown(isolated_db):
    user_id = _reseller("active")
    with write_session() as db:
        db.get(PanelUser, user_id).disabled = True  # panel user disabled by an administrator
    assert _login(username="resel1") == {"valid": False}
    assert _session_count(user_id) == 0


@pytest.mark.parametrize("route,form", [
    ("/login", {"username": "cust1", "password": PASSWORD}),
    ("/login/2fa", {"pending_token": "pending", "code": "123456"}),
])
def test_daemon_lockout_429_carries_retry_after(isolated_db, monkeypatch, route, form):
    import warnings

    warnings.filterwarnings("ignore")
    from testsupport.sync_asgi import SyncASGIClient as TestClient

    import api.main as main
    from api import ratelimit
    from api.routers import auth as auth_router

    monkeypatch.setattr(ratelimit, "limiter", ratelimit.SlidingWindowLimiter())
    monkeypatch.setattr(auth_router, "call_daemon_anonymous",
                        lambda op, **_params: {"locked": True, "retry_after_seconds": 125})
    response = TestClient(main.app, follow_redirects=False).post(route, data=form)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "125"
    assert response.json()["detail"] == "too many failed attempts -- try again in 3 minute(s)"
