"""Privileged RPC failures must not publish secrets from handlers or inputs."""
import asyncio

import pytest

from daemon import server
from shared.validation import ValidationError


@pytest.fixture(autouse=True)
def synchronous_dispatch_executor(monkeypatch):
    # This suite tests the error boundary, not thread scheduling. The
    # restricted test sandbox can stall asyncio.run() while shutting down its
    # default executor after a real run_in_executor call.
    def run_immediately(loop, _executor, handler, params):
        future = loop.create_future()
        try:
            future.set_result(handler(params))
        except Exception as exc:
            future.set_exception(exc)
        return future

    monkeypatch.setattr(asyncio.BaseEventLoop, "run_in_executor", run_immediately)
    from daemon.rpc_authority import Principal
    monkeypatch.setattr(server, "resolve_principal", lambda _: Principal("admin", "admin", None, 1, "session"))
    for op in ("test.secret_failure", "test.validation_failure", "test.safe_validation"):
        monkeypatch.setitem(server.POLICY_BY_OPERATION, op, "global_admin")


@pytest.mark.parametrize("exception,public_message", [
    (RuntimeError("generated database password: GeneratedSecret123"), "internal operation failure"),
    (ValueError("generated database password: GeneratedSecret123"), "invalid request"),
])
def test_dispatch_does_not_log_audit_or_return_vendor_secrets(monkeypatch, caplog, exception, public_message):
    records = []

    def fail(_params):
        raise exception

    monkeypatch.setitem(server.OP_TABLE, "test.secret_failure", fail)
    monkeypatch.setattr(server.audit, "record", lambda *args: records.append(args))
    with pytest.raises(type(exception), match=public_message) as raised:
        asyncio.run(server.dispatch("test.secret_failure", {"_actor": "admin", "_role": "admin"}))
    assert "GeneratedSecret123" not in str(raised.value)
    assert "GeneratedSecret123" not in caplog.text
    assert len(records) == 1
    assert "GeneratedSecret123" not in str(records[0])


def test_dispatch_redacts_secret_in_validation_detail(monkeypatch):
    records = []

    def fail(_params):
        raise ValidationError("password XSecret! is invalid")

    monkeypatch.setitem(server.OP_TABLE, "test.validation_failure", fail)
    monkeypatch.setattr(server.audit, "record", lambda *args: records.append(args))
    with pytest.raises(ValidationError, match=r"password \*\*\* is invalid") as raised:
        asyncio.run(server.dispatch("test.validation_failure", {
            "_actor": "admin", "_role": "admin", "password": "XSecret!",
        }))
    assert "XSecret!" not in str(raised.value)
    assert "XSecret!" not in records[0][-1]


def test_dispatch_retains_safe_validation_feedback(monkeypatch):
    records = []

    def fail(_params):
        raise ValidationError("domain name is invalid")

    monkeypatch.setitem(server.OP_TABLE, "test.safe_validation", fail)
    monkeypatch.setattr(server.audit, "record", lambda *args: records.append(args))
    with pytest.raises(ValidationError, match="domain name is invalid"):
        asyncio.run(server.dispatch("test.safe_validation", {"_actor": "admin", "_role": "admin"}))
    assert records[0][-1] == "domain name is invalid"
