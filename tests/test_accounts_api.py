from api.routers import accounts
from api.security import Identity


ADMIN = Identity(panel_user_id=1, username="admin", role="admin", account_id=None, auth_method="session")


def test_create_with_plan_preserves_one_time_password(monkeypatch):
    def fake_call(op, identity, **params):
        assert identity is ADMIN
        if op == "account.create":
            return {"username": "demo1", "status": "active", "initial_password": "Secret!234567", "email": None}
        assert op == "plan.apply"
        return {"username": "demo1", "status": "active", "plan_id": params["plan_id"]}

    monkeypatch.setattr(accounts, "call_daemon", fake_call)
    result = accounts.create_account(accounts.CreateAccountBody(username="demo1", plan_id=7), ADMIN)

    assert result["plan_id"] == 7
    assert result["initial_password"] == "Secret!234567"


def test_create_returns_credentials_when_plan_application_fails(monkeypatch):
    def fake_call(op, identity, **params):
        if op == "account.create":
            return {"username": "demo1", "status": "active", "initial_password": "Secret!234567", "email": None}
        raise RuntimeError("quota service unavailable")

    monkeypatch.setattr(accounts, "call_daemon", fake_call)
    result = accounts.create_account(accounts.CreateAccountBody(username="demo1", plan_id=7), ADMIN)

    assert result["username"] == "demo1"
    assert result["initial_password"] == "Secret!234567"
    assert result["plan_apply_error"] == "quota service unavailable"
