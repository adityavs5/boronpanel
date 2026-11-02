from api.routers import accounts
from api.security import Identity


ADMIN = Identity(panel_user_id=1, username="admin", role="admin", account_id=None, auth_method="session")


def test_create_with_plan_preserves_one_time_password(monkeypatch):
    def fake_call(op, identity, **params):
        assert identity is ADMIN
        if op == "account.create":
            return {"username": "demo1", "status": "active", "initial_password": "Secret!234567", "email": None}
        if op == "plan.apply":
            return {"username": "demo1", "status": "active", "plan_id": params["plan_id"]}
        assert op == "ipmanager.assign_new"
        return {"username": "demo1", "address": "192.0.2.10"}

    monkeypatch.setattr(accounts, "call_daemon", fake_call)
    result = accounts.create_account(accounts.CreateAccountBody(username="demo1", plan_id=7), ADMIN)

    assert result["plan_id"] == 7
    assert result["initial_password"] == "Secret!234567"


def test_create_returns_credentials_when_plan_application_fails(monkeypatch):
    def fake_call(op, identity, **params):
        if op == "account.create":
            return {"username": "demo1", "status": "active", "initial_password": "Secret!234567", "email": None}
        if op == "plan.apply":
            raise RuntimeError("quota service unavailable")
        return {"username": "demo1", "address": "192.0.2.10"}

    monkeypatch.setattr(accounts, "call_daemon", fake_call)
    result = accounts.create_account(accounts.CreateAccountBody(username="demo1", plan_id=7), ADMIN)

    assert result["username"] == "demo1"
    assert result["initial_password"] == "Secret!234567"
    assert result["plan_apply_error"] == "quota service unavailable"


def test_create_passes_specific_ip_allocation(monkeypatch):
    calls = []
    def fake_call(op, identity, **params):
        calls.append((op, params))
        if op == "account.create":
            return {"username": "demo1", "initial_password": "Secret!234567"}
        return {"username": "demo1", "server_ip_id": params["server_ip_id"], "address": "192.0.2.20"}

    monkeypatch.setattr(accounts, "call_daemon", fake_call)
    result = accounts.create_account(accounts.CreateAccountBody(username="demo1", ip_selection="specific", server_ip_id=4), ADMIN)
    assert calls[-1] == ("ipmanager.assign_new", {"username": "demo1", "selection": "specific", "server_ip_id": 4})
    assert result["server_ip"]["address"] == "192.0.2.20"


def test_create_with_unknown_plan_stops_before_anything_else(monkeypatch):
    """The plan is validated by account.create itself, before provisioning."""
    import pytest
    from fastapi import HTTPException
    calls = []

    def fake_call(op, identity, **params):
        calls.append((op, params))
        raise HTTPException(status_code=404, detail="plan 999999 not found")

    monkeypatch.setattr(accounts, "call_daemon", fake_call)
    with pytest.raises(HTTPException) as raised:
        accounts.create_account(accounts.CreateAccountBody(username="demo9", plan_id=999999), ADMIN)
    assert raised.value.status_code == 404
    assert calls == [("account.create", {"username": "demo9", "plan_id": 999999})]


def test_reseller_delete_route_forwards_release_choice(monkeypatch):
    from api.routers import resellers
    calls = []
    monkeypatch.setattr(resellers, "call_daemon", lambda op, identity, **params: calls.append((op, params)) or {"status": "deleted"})
    assert resellers.delete_reseller(5, True, ADMIN)["status"] == "deleted"
    assert calls == [("reseller.delete", {"reseller_id": 5, "release_accounts": True})]


def test_limits_route_sends_only_the_edited_fields(monkeypatch):
    """A null field keeps the enforced value (possibly Unlimited) instead of
    forcing the form to submit a number for every limit."""
    calls = []
    monkeypatch.setattr(accounts, "call_daemon", lambda op, identity, **params: calls.append((op, params)) or {})
    accounts.set_limits("demo1", accounts.SetLimitsBody(mem_mb=768, cpu_pct=None), ADMIN)
    assert calls == [("account.set_limits", {"username": "demo1", "mem_mb": 768})]
