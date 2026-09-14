import pytest
from fastapi import HTTPException

from api.routers import ipmanager
from api.security import Identity

ADMIN = Identity(panel_user_id=1, username="admin", role="admin", account_id=None, auth_method="session")
CUSTOMER = Identity(panel_user_id=2, username="customer", role="customer", account_id=2, auth_method="session")


def test_customer_cannot_read_or_change_server_ip_inventory(monkeypatch):
    monkeypatch.setattr(ipmanager, "call_daemon", lambda *args, **kwargs: pytest.fail("daemon must not be called"))
    with pytest.raises(HTTPException) as exc:
        ipmanager.get_state(CUSTOMER)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        ipmanager.assign_account("demo1", ipmanager.AssignmentBody(selection="primary"), CUSTOMER)
    assert exc.value.status_code == 403


def test_admin_assignment_dispatches_validated_shape(monkeypatch):
    captured = {}
    def fake_call(op, identity, **params):
        captured.update(op=op, identity=identity, params=params)
        return {"status": "ok"}
    monkeypatch.setattr(ipmanager, "call_daemon", fake_call)
    result = ipmanager.assign_account("demo1", ipmanager.AssignmentBody(selection="specific", server_ip_id=7), ADMIN)
    assert result == {"status": "ok"}
    assert captured == {"op": "ipmanager.assign", "identity": ADMIN, "params": {"username": "demo1", "selection": "specific", "server_ip_id": 7}}
