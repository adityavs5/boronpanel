import pytest
from fastapi import HTTPException

from api.routers import firewall, olsadmin, ssl_router
from api.security import Identity


ADMIN = Identity(1, "admin", "admin", None, "session")
CUSTOMER = Identity(2, "customer", "customer", 1, "session")


@pytest.mark.parametrize("handler", [
    firewall.list_bypass,
    olsadmin.status,
    ssl_router.admin_ssl_dashboard,
])
def test_new_server_controls_reject_customer_before_rpc(handler, monkeypatch):
    module = __import__(handler.__module__, fromlist=["call_daemon"])
    monkeypatch.setattr(module, "call_daemon", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("RPC reached")))
    with pytest.raises(HTTPException) as error:
        handler(identity=CUSTOMER)
    assert error.value.status_code == 403


def test_new_server_controls_dispatch_expected_admin_operations(monkeypatch):
    calls = []

    def rpc(operation, _identity, **params):
        calls.append((operation, params))
        return {"ok": True}

    monkeypatch.setattr(firewall, "call_daemon", rpc)
    monkeypatch.setattr(olsadmin, "call_daemon", rpc)
    monkeypatch.setattr(ssl_router, "call_daemon", rpc)
    firewall.add_bypass(firewall.BypassBody(address="198.51.100.42", label="office"), identity=ADMIN)
    olsadmin.update_settings(olsadmin.SettingsBody(
        max_connections=10000, max_ssl_connections=10000, connection_timeout=300,
        keep_alive_timeout=5, max_keep_alive_requests=10000, gzip_level=6,
        brotli_level=6, gzip_enabled=True, brotli_enabled=True, quic_enabled=True,
        log_level="WARN", log_keep_days=30,
    ), identity=ADMIN)
    ssl_router.admin_issue_certificate("example.test", ssl_router.DomainIssueCertBody(force=True), identity=ADMIN)
    assert calls[0] == ("firewall.bypass.add", {"address": "198.51.100.42", "label": "office"})
    assert calls[1][0] == "ols.admin.settings.update"
    assert calls[2] == ("ssl.issue", {"domain": "example.test", "force": True})
