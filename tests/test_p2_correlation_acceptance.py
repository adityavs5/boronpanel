from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from api import logsetup as api_logsetup
from daemon import server
from daemon.logsetup import configure_logging
from daemon.rpc_authority import AuthorizationError
from shared.db import write_session
from shared.failures import OperationFailure
from shared.models import AuditLog
from shared.request_context import request_context
from shared.validation import ValidationError

pytestmark = pytest.mark.usefixtures("immediate_asyncio_executor")


@pytest.mark.parametrize(
    ("kind", "expected"),
    (("validation", ValidationError), ("permission", AuthorizationError), ("unexpected", OperationFailure)),
)
def test_validation_permission_and_rpc_failure_share_reference_across_logs_and_audit(
    kind, expected, isolated_db, tmp_path, monkeypatch
):
    log_dir = tmp_path / kind
    monkeypatch.setattr("daemon.logsetup.pwd.getpwnam", lambda _: SimpleNamespace(pw_uid=0, pw_gid=0))
    configure_logging(str(log_dir))
    monkeypatch.setattr(api_logsetup.settings, "log_dir", str(log_dir))
    api_logsetup.reset()

    principal = SimpleNamespace(username="admin", role="admin")
    monkeypatch.setattr(server, "resolve_principal", lambda _credential: principal)

    if kind == "permission":
        def reject(*_args, **_kwargs):
            raise AuthorizationError("admin role required")
        monkeypatch.setattr(server, "authorize", reject)
        handler = lambda _params: {"ok": True}
    else:
        monkeypatch.setattr(server, "authorize", lambda *_args, **_kwargs: None)
        if kind == "validation":
            def handler(_params):
                raise ValidationError("domain is invalid")
        else:
            def handler(_params):
                raise RuntimeError("vendor detail must not escape")

    monkeypatch.setitem(server.OP_TABLE, "p2.acceptance", handler)
    request_id = f"p2-{kind}-request"
    with request_context(request_id):
        with pytest.raises(expected) as caught:
            asyncio.run(server.dispatch("p2.acceptance", {"username": "demo"}, {"type": "test"}))
        diagnostic = caught.value.diagnostic
        api_logsetup.record_operation_error(
            diagnostic, "admin", "127.0.0.1", status=502, request_id=request_id
        )

    reference = diagnostic["reference"]
    assert len(reference) == 16
    with write_session() as db:
        audit = db.query(AuditLog).filter(AuditLog.request_id == request_id).one()
        assert reference in (audit.detail or "")

    daemon_rows = [json.loads(line) for line in (log_dir / "daemon.log").read_text().splitlines()]
    assert any(row.get("request_id") == request_id and reference in row.get("message", "") for row in daemon_rows)
    api_rows = api_logsetup.search_error_records(
        reference=reference, request_id=request_id, log_dir=str(log_dir)
    )
    assert api_rows and api_rows[0]["reference"] == reference
    assert "vendor detail" not in json.dumps(daemon_rows + api_rows)
