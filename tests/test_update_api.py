"""API surface of the panel update system: /api/v1/admin/update/*.

The router forwards confirmation proof to borond, where the root policy
verifies it. These tests inspect that forwarding with a stubbed RPC call.
"""
from __future__ import annotations

import warnings

import pytest

warnings.filterwarnings("ignore")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

import api.main as main  # noqa: E402
import api.routers.update as update_router  # noqa: E402
from api.security import Identity, get_identity  # noqa: E402

ADMIN = Identity(panel_user_id=1, username="adminuser", role="admin",
                 account_id=None, auth_method="session")
CUSTOMER = Identity(panel_user_id=2, username="custuser", role="customer",
                    account_id=7, auth_method="session")
TOKEN_ADMIN = Identity(panel_user_id=-1, username="billing-bot", role="admin",
                       account_id=None, auth_method="token")


@pytest.fixture
def as_identity():
    def _set(identity: Identity) -> TestClient:
        main.app.dependency_overrides[get_identity] = lambda: identity
        return TestClient(main.app)

    yield _set
    main.app.dependency_overrides.pop(get_identity, None)


@pytest.fixture
def daemon_stub(monkeypatch):
    """Scriptable call_daemon: records calls, answers from a canned table."""
    calls = []
    answers = {
        "update.status": {"configured": True, "update_available": False},
        "update.check": {"configured": True, "update_available": False},
        "update.start": {"id": 1, "status": "pending", "kind": "update"},
        "update.rollback": {"id": 2, "status": "pending", "kind": "rollback"},
        "update.log": {"job": None, "log": []},
        "update.history": {"jobs": []},
    }

    def fake_call(op, identity, **params):
        calls.append((op, params))
        return answers[op]

    monkeypatch.setattr(update_router, "call_daemon", fake_call)
    return {"calls": calls, "answers": answers}


def test_all_admin_update_endpoints_reject_customers(as_identity, daemon_stub):
    client = as_identity(CUSTOMER)
    assert client.get("/api/v1/admin/update/status").status_code == 403
    assert client.post("/api/v1/admin/update/check").status_code == 403
    assert client.post("/api/v1/admin/update/start", json={"confirm": True}).status_code == 403
    assert client.post("/api/v1/admin/update/rollback", json={"confirm": True}).status_code == 403
    assert client.get("/api/v1/admin/update/log").status_code == 403
    assert client.get("/api/v1/admin/update/history").status_code == 403
    assert daemon_stub["calls"] == []  # rejected before any RPC


def test_status_and_history_plumb_through(as_identity, daemon_stub):
    client = as_identity(ADMIN)
    assert client.get("/api/v1/admin/update/status").json()["configured"] is True
    assert client.get("/api/v1/admin/update/history").json() == {"jobs": []}
    assert client.post("/api/v1/admin/update/check").status_code == 200
    ops = [op for op, _ in daemon_stub["calls"]]
    assert ops == ["update.status", "update.history", "update.check"]
    # Check-now must bypass the cache.
    assert daemon_stub["calls"][2][1]["force"] is True


def test_start_requires_explicit_confirm(as_identity, daemon_stub):
    client = as_identity(ADMIN)
    r = client.post("/api/v1/admin/update/start", json={})
    assert r.status_code == 400 and "confirm" in r.json()["detail"]
    assert daemon_stub["calls"] == []


def test_start_rejects_api_tokens(as_identity, daemon_stub):
    client = as_identity(TOKEN_ADMIN)
    r = client.post("/api/v1/admin/update/start", json={"confirm": True})
    assert r.status_code == 400
    assert "browser session" in r.json()["detail"]
    assert daemon_stub["calls"] == []


def test_start_without_2fa_enabled_proceeds(as_identity, daemon_stub):
    client = as_identity(ADMIN)
    r = client.post("/api/v1/admin/update/start", json={"confirm": True})
    assert r.status_code == 200 and r.json()["status"] == "pending"
    ops = dict(daemon_stub["calls"])
    assert ops["update.start"]["initiated_by"] == "adminuser"
    assert ops["update.start"]["confirm"] is True
    assert ops["update.start"]["totp_code"] is None


def test_start_forwards_second_factor_to_root(as_identity, daemon_stub):
    client = as_identity(ADMIN)
    r = client.post("/api/v1/admin/update/start",
                    json={"confirm": True, "totp_code": "123456"})
    assert r.status_code == 200
    assert daemon_stub["calls"] == [("update.start", {
        "initiated_by": "adminuser", "to_version": None,
        "confirm": True, "totp_code": "123456",
    })]


def test_rollback_forwards_second_factor_to_root(as_identity, daemon_stub):
    client = as_identity(ADMIN)
    r = client.post("/api/v1/admin/update/rollback",
                    json={"confirm": True, "totp_code": "123456"})
    assert r.status_code == 200 and r.json()["kind"] == "rollback"
    assert daemon_stub["calls"] == [("update.rollback", {
        "initiated_by": "adminuser", "confirm": True, "totp_code": "123456",
    })]


def test_log_passes_job_id(as_identity, daemon_stub):
    client = as_identity(ADMIN)
    r = client.get("/api/v1/admin/update/log?job_id=7")
    assert r.status_code == 200
    assert dict(daemon_stub["calls"])["update.log"]["job_id"] == 7
