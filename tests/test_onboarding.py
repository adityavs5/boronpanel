from __future__ import annotations

import warnings

import pytest

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient

import api.main as main
from api.security import Identity, get_identity
from daemon import handlers_account as ha
from daemon import onboarding
from shared.config import settings


@pytest.fixture()
def stub_sysops(monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    monkeypatch.setattr(ha.handlers_domain, "ensure_docroot", lambda username, docroot, domain_name=None: None)
    monkeypatch.setattr(ha.handlers_domain, "_create_managed_zone", lambda username, domain: {"zone": domain})
    monkeypatch.setattr(ha.handlers_domain, "_delete_managed_zone", lambda domain: {"zone": domain})
    monkeypatch.setattr(ha.ols, "provision_vhost", lambda account: None)


def test_fresh_account_is_not_completed(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    data = onboarding.get_onboarding({"username": "demo1"})
    assert data["completed"] is False
    assert data["skipped"] is False
    assert data["completed_at"] is None


def test_get_includes_server_facts(isolated_db, stub_sysops, monkeypatch):
    monkeypatch.setattr(settings, "server_public_ip", "203.0.113.10")
    ha.create_account({"username": "demo1", "primary_domain": "example.com"})
    data = onboarding.get_onboarding({"username": "demo1"})
    assert data["server_ip"] == "203.0.113.10"
    assert data["primary_domain"] == "example.com"
    assert data["nameservers"] == ["ns1.example.com", "ns2.example.com"]


def test_no_primary_domain_means_no_nameservers(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    data = onboarding.get_onboarding({"username": "demo1"})
    assert data["nameservers"] == []


def test_complete_marks_once_only(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    result = onboarding.set_onboarding({"username": "demo1", "completed": True, "skipped": False})
    assert result["completed"] is True
    assert result["skipped"] is False
    assert result["completed_at"] is not None

    # Re-marking with skipped=True must NOT overwrite the original record --
    # completion is one-way and first-write-wins. Compare without the tz
    # suffix: same value renders with/without "+00:00" depending on whether
    # it was read in the same flush or from a fresh SELECT (the same
    # pre-existing SQLite DateTime(timezone=True) quirk test_plans.py
    # documents).
    again = onboarding.set_onboarding({"username": "demo1", "completed": True, "skipped": True})
    assert again["completed"] is True
    assert again["skipped"] is False
    assert again["completed_at"].replace("+00:00", "") == result["completed_at"].replace("+00:00", "")


def test_skip_records_skipped(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    result = onboarding.set_onboarding({"username": "demo1", "completed": True, "skipped": True})
    assert result["completed"] is True
    assert result["skipped"] is True


def test_set_completed_false_is_noop(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    result = onboarding.set_onboarding({"username": "demo1", "completed": False})
    assert result["completed"] is False


def test_unknown_account_raises(isolated_db):
    with pytest.raises(Exception):
        onboarding.get_onboarding({"username": "ghost"})


# --- API scoping -------------------------------------------------------------


def test_api_customer_cannot_touch_other_account(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    ha.create_account({"username": "demo2"})
    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import Account

    with write_session() as db:
        demo2_id = db.scalar(select(Account.id).where(Account.username == "demo2"))

    customer = Identity(panel_user_id=2, username="demo2", role="customer", account_id=demo2_id, auth_method="session")
    main.app.dependency_overrides[get_identity] = lambda: customer
    try:
        client = TestClient(main.app)
        r = client.get("/api/v1/accounts/demo1/onboarding")
        assert r.status_code == 403
        r = client.patch("/api/v1/accounts/demo1/onboarding", json={"completed": True})
        assert r.status_code == 403
    finally:
        main.app.dependency_overrides.pop(get_identity, None)


def test_api_unauthenticated_rejected(isolated_db):
    client = TestClient(main.app)
    assert client.get("/api/v1/accounts/demo1/onboarding").status_code == 401
