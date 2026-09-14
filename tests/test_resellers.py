"""Reseller plans, ownership boundaries, and reseller-panel API coverage."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api.security import Identity, require_account_access, require_domain_access
from daemon import resellers
from shared.db import write_session
from shared.models import Account, Domain, PanelUser, ResellerAccount, ResellerPlan, ResellerProfile
from shared.validation import ValidationError


def make_plan(**overrides):
    values = {
        "name": "Starter reseller",
        "max_accounts": 2,
        "max_total_disk_mb": 12288,
        "account_quota_soft_mb": 4096,
        "account_quota_hard_mb": 5120,
        "account_cpu_pct": 50,
        "account_mem_mb": 1024,
        "account_io_mb": 50,
        "account_pids_max": 100,
        "php_version": "8.3",
    }
    values.update(overrides)
    return resellers.create_plan(values)


def make_profile(plan_id: int, username: str = "reseller1") -> tuple[int, int]:
    with write_session() as session:
        user = PanelUser(username=username, password_hash="hash", role="reseller", account_id=None)
        session.add(user)
        session.flush()
        profile = ResellerProfile(panel_user_id=user.id, plan_id=plan_id, company="Example Hosting")
        session.add(profile)
        session.flush()
        return user.id, profile.id


def make_owned_account(profile_id: int, username: str, hard_mb: int = 5120, domain: str | None = None) -> int:
    with write_session() as session:
        account = Account(
            username=username, status="active", php_version="8.3",
            quota_soft_mb=min(4096, hard_mb), quota_hard_mb=hard_mb,
            cpu_pct=50, mem_mb=1024, io_mb=50, pids_max=100,
            primary_domain=domain,
        )
        session.add(account)
        session.flush()
        if domain:
            session.add(Domain(account_id=account.id, domain=domain, kind="primary", docroot=f"/home/{username}/public_html"))
        session.add(ResellerAccount(reseller_id=profile_id, account_id=account.id))
        session.flush()
        return account.id


def test_plan_validation_uses_field_names_and_account_minimums(isolated_db):
    with pytest.raises(ValidationError, match="account_mem_mb must be between 64"):
        make_plan(account_mem_mb=32)
    with pytest.raises(ValidationError, match="account_pids_max must be between 10"):
        make_plan(account_pids_max=5)


def test_plan_update_cannot_drop_below_current_allocation(isolated_db):
    plan = make_plan(max_accounts=3, max_total_disk_mb=20000)
    _, profile_id = make_profile(plan["id"])
    make_owned_account(profile_id, "ownedone", hard_mb=6000)
    make_owned_account(profile_id, "ownedtwo", hard_mb=6000)

    with pytest.raises(resellers.ResellerError, match="currently has 2"):
        resellers.update_plan({"plan_id": plan["id"], "max_accounts": 1})
    with pytest.raises(resellers.ResellerError, match="currently allocates 12000"):
        resellers.update_plan({"plan_id": plan["id"], "max_total_disk_mb": 10000})


def test_plan_delete_rejects_assigned_plan(isolated_db):
    plan = make_plan()
    make_profile(plan["id"])
    with pytest.raises(resellers.ResellerError, match="assigned"):
        resellers.delete_plan({"plan_id": plan["id"]})


def test_create_reseller_creates_role_and_returns_password_once(isolated_db):
    plan = make_plan()
    result = resellers.create_reseller({"username": "sellerone", "company": "Seller One", "plan_id": plan["id"]})
    assert result["username"] == "sellerone"
    assert result["initial_password"]
    listed = resellers.list_resellers({})["resellers"][0]
    assert "initial_password" not in listed
    with write_session() as session:
        user = session.scalar(select(PanelUser).where(PanelUser.username == "sellerone"))
        assert user.role == "reseller"
        assert result["initial_password"] not in user.password_hash


def test_reassign_plan_rejects_insufficient_capacity(isolated_db):
    large = make_plan(name="Large", max_accounts=4, max_total_disk_mb=30000)
    small = make_plan(name="Small", max_accounts=1, max_total_disk_mb=6000)
    _, profile_id = make_profile(large["id"])
    make_owned_account(profile_id, "ownedone", hard_mb=5000)
    make_owned_account(profile_id, "ownedtwo", hard_mb=5000)
    with pytest.raises(resellers.ResellerError, match="currently has 2"):
        resellers.update_reseller({"reseller_id": profile_id, "plan_id": small["id"]})


def test_dashboard_only_lists_owned_nonterminated_accounts(isolated_db):
    plan = make_plan()
    _, profile_id = make_profile(plan["id"])
    make_owned_account(profile_id, "ownedone")
    with write_session() as session:
        session.add(Account(username="somebody", status="active", php_version="8.3"))
    data = resellers.dashboard({"reseller_username": "reseller1"})
    assert [row["username"] for row in data["accounts"]] == ["ownedone"]
    assert data["usage"] == {"accounts": 1, "disk_mb": 5120}


def test_create_account_uses_plan_defaults_and_records_ownership(isolated_db, monkeypatch):
    plan = make_plan()
    _, profile_id = make_profile(plan["id"])
    captured = {}

    def fake_create(params):
        captured.update(params)
        with write_session() as session:
            account = Account(
                username=params["username"], status="active", primary_domain=params.get("primary_domain"),
                php_version=params["php_version"], quota_soft_mb=params["quota_soft_mb"],
                quota_hard_mb=params["quota_hard_mb"], cpu_pct=params["cpu_pct"],
                mem_mb=params["mem_mb"], io_mb=params["io_mb"], pids_max=params["pids_max"],
            )
            session.add(account)
            session.flush()
            return {"id": account.id, "username": account.username, "status": account.status}

    monkeypatch.setattr(resellers.handlers_account, "create_account", fake_create)
    monkeypatch.setattr(resellers.audit, "record_account_event", lambda *args, **kwargs: None)
    result = resellers.create_account({
        "reseller_username": "reseller1", "username": "newowned", "password": "StrongPass123!"
    })
    assert result["initial_password"] == "StrongPass123!"
    assert captured["quota_hard_mb"] == plan["account_quota_hard_mb"]
    assert captured["mem_mb"] == plan["account_mem_mb"]
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "newowned"))
        ownership = session.scalar(select(ResellerAccount).where(ResellerAccount.account_id == account.id))
        user = session.scalar(select(PanelUser).where(PanelUser.username == "newowned"))
        assert ownership.reseller_id == profile_id
        assert user.role == "customer" and user.account_id == account.id


def test_create_account_enforces_count_limit(isolated_db):
    plan = make_plan(max_accounts=1)
    _, profile_id = make_profile(plan["id"])
    make_owned_account(profile_id, "ownedone")
    with pytest.raises(resellers.ResellerError, match="account limit"):
        resellers.create_account({"reseller_username": "reseller1", "username": "secondone"})


def test_create_account_compensates_when_panel_login_fails(isolated_db, monkeypatch):
    plan = make_plan()
    make_profile(plan["id"])
    terminated = []
    monkeypatch.setattr(resellers.handlers_account, "create_account", lambda params: {"id": 77, "username": params["username"], "status": "active"})
    monkeypatch.setattr(resellers.handlers_auth, "create_panel_user", lambda params: (_ for _ in ()).throw(RuntimeError("login write failed")))
    monkeypatch.setattr(resellers.handlers_account, "terminate_account", lambda params: terminated.append(params["username"]))
    with pytest.raises(RuntimeError, match="login write failed"):
        resellers.create_account({
            "reseller_username": "reseller1", "username": "rollbackme", "password": "StrongPass123!"
        })
    assert terminated == ["rollbackme"]


def test_lifecycle_rechecks_ownership_before_handler(isolated_db, monkeypatch):
    plan = make_plan()
    make_profile(plan["id"])
    called = []
    monkeypatch.setattr(resellers.handlers_account, "suspend_account", lambda params: called.append(params))
    with pytest.raises(resellers.ResellerError, match="not owned"):
        resellers.lifecycle({"reseller_username": "reseller1", "username": "stranger", "action": "suspend"})
    assert called == []


def test_security_helpers_scope_reseller_to_owned_account_and_domain(isolated_db):
    plan = make_plan()
    panel_user_id, profile_id = make_profile(plan["id"])
    make_owned_account(profile_id, "ownedone", domain="owned.example")
    identity = Identity(panel_user_id, "reseller1", "reseller", None, "session")
    require_account_access(identity, "ownedone")
    require_domain_access(identity, "owned.example")
    with pytest.raises(Exception) as account_error:
        require_account_access(identity, "stranger")
    with pytest.raises(Exception) as domain_error:
        require_domain_access(identity, "stranger.example")
    assert account_error.value.status_code == 403
    assert domain_error.value.status_code == 403


def test_reseller_api_role_boundaries_and_identity_forwarding(isolated_db, monkeypatch):
    from api.routers import resellers as router

    calls = []
    monkeypatch.setattr(router, "call_daemon", lambda op, identity, **params: calls.append((op, params)) or {"plans": []})
    admin = Identity(1, "admin", "admin", None, "session")
    reseller_identity = Identity(2, "seller", "reseller", None, "session")
    customer = Identity(3, "customer", "customer", 1, "session")
    assert router.plans(admin) == {"plans": []}
    with pytest.raises(HTTPException) as denied:
        router.plans(customer)
    assert denied.value.status_code == 403
    assert router.dashboard(reseller_identity) == {"plans": []}
    assert calls[-1] == ("reseller.dashboard", {"reseller_username": "seller"})
    with pytest.raises(HTTPException):
        router.plans(reseller_identity)
