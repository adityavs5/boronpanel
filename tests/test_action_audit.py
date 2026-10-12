from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, select

from api import security as sec
from api.routers import auditlog
from daemon import audit, identity_admin, server
from shared.db import _apply_additive_migrations, write_session
from shared.failures import OperationFailure
from shared.models import Account, ActionStep, AuditLog, utcnow
from shared.request_context import request_context

pytestmark = pytest.mark.usefixtures("immediate_asyncio_executor")


def _account(username="oldname"):
    with write_session() as db:
        row = Account(
            username=username, status="active", uid=5001, gid=5001,
            primary_domain=None, cpu_pct=25, mem_mb=512, io_mb=50, pids_max=50,
        )
        db.add(row)
        db.flush()
        return row.id


def _rename_system(monkeypatch, *, fail_on=None):
    def operation(name):
        def invoke(*_args, **_kwargs):
            if fail_on == name:
                raise RuntimeError("forced rename failure")
        return invoke

    monkeypatch.setattr(identity_admin.sysops, "user_exists", lambda _name: False)
    monkeypatch.setattr(identity_admin.sysops, "rename_login", operation("rename_login"))
    monkeypatch.setattr(identity_admin.sysops, "rename_group", operation("rename_group"))
    monkeypatch.setattr(identity_admin.sysops, "move_home", operation("move_home"))
    monkeypatch.setattr(identity_admin.sysops, "recycle_php_workers", operation("recycle"))
    monkeypatch.setattr(identity_admin.cgroups, "apply_limits", operation("limits"))
    monkeypatch.setattr(identity_admin.ols, "refresh_vhost", operation("vhost"))
    monkeypatch.setattr(identity_admin, "_refresh_git_paths", operation("git"))
    monkeypatch.setattr(identity_admin, "_pause_for_rename", lambda _account_id: operation("resume"))


def _dispatch_rename(monkeypatch, *, fail_on=None):
    _rename_system(monkeypatch, fail_on=fail_on)
    principal = SimpleNamespace(username="admin", role="admin")
    monkeypatch.setattr(server, "resolve_principal", lambda _credential: principal)
    monkeypatch.setattr(server, "authorize", lambda *_args, **_kwargs: None)
    with request_context("rename-request-1"):
        return asyncio.run(server.dispatch(
            "account.rename",
            {"username": "oldname", "new_username": "newname", "_ip": "192.0.2.44"},
            {"type": "test"},
        ))


def test_rename_audit_has_ip_duration_diff_and_steps(isolated_db, monkeypatch):
    _account()
    result = _dispatch_rename(monkeypatch)
    assert result["new_username"] == "newname"

    with write_session() as db:
        row = db.scalar(select(AuditLog).where(AuditLog.op == "account.rename"))
        steps = db.scalars(select(ActionStep).where(ActionStep.audit_id == row.id).order_by(ActionStep.id)).all()
        assert row.actor == "admin"
        assert row.source_ip == "192.0.2.44"
        assert row.request_id == "rename-request-1"
        assert row.duration_ms is not None and row.duration_ms >= 0
        assert row.changes["username"] == {"before": "oldname", "after": "newname"}
        assert steps
        assert all(item.result == "ok" for item in steps)
        assert "rename: move home directory" in [item.name for item in steps]


def test_all_first_wave_mutations_are_trace_enabled():
    expected = {
        "account.rename", "account.set_primary_domain", "account.terminate",
        "domain.add", "firewall.add", "firewall.delete", "namespace.rebuild",
        "backup.job.trigger", "backup.restore.trigger", "snapshot.policy.run",
        "snapshot.restore.trigger", "ssl.issue", "ssl.issue_wildcard",
        "dns.set_record", "dns.delete_record",
    }
    assert expected <= server.TRACED_ACTION_OPS
    assert all(operation in server.OP_TABLE for operation in expected)


def test_rename_forced_midway_failure_keeps_failed_step_and_rollbacks(isolated_db, monkeypatch):
    _account()
    with pytest.raises(OperationFailure):
        _dispatch_rename(monkeypatch, fail_on="move_home")

    with write_session() as db:
        account = db.scalar(select(Account))
        assert account.username == "oldname"
        row = db.scalar(select(AuditLog).where(AuditLog.op == "account.rename"))
        steps = db.scalars(select(ActionStep).where(ActionStep.audit_id == row.id).order_by(ActionStep.id)).all()
        assert row.result == "failed"
        assert row.source_ip == "192.0.2.44"
        assert row.changes["username"]["after"] == "newname"
        failed = [item for item in steps if item.result == "failed"]
        assert [item.name for item in failed] == ["rename: move home directory"]
        names = [item.name for item in steps]
        assert "rename: restore Linux group" in names
        assert "rename: restore Linux login" in names


def test_step_output_is_bounded_and_redacts_secrets_keys_and_mail(isolated_db):
    secret_value = "".join(("NeverStore", "This", "Credential!"))
    token = "token-value-that-must-not-survive"
    private_key = "-----BEGIN " + "PRIVATE KEY-----\nprivate-key-material\n-----END " + "PRIVATE KEY-----"
    mail = "From: sender@example.test\nTo: recipient@example.test\nSubject: private\n\nmail body"
    with audit.action_trace("test.redaction", {"password": secret_value, "api_token": token}) as trace:
        audit.change("api_token", token, "replacement-token")
        with audit.step("test: capture output") as capture:
            capture.stdout = "x" * 5000 + f"\npassword={secret_value}\ntoken={token}\n{private_key}"
            capture.stderr = mail
            capture.exit_code = 17
        ident = audit.record(
            "admin", "admin", "test.redaction", "demo", {"password": secret_value},
            "failed", source_ip="198.51.100.9", duration_ms=12, trace=trace,
        )

    with write_session() as db:
        row = db.get(AuditLog, ident)
        item = db.scalar(select(ActionStep).where(ActionStep.audit_id == ident))
        stored = json.dumps({"params": row.params, "changes": row.changes,
                             "stdout": item.stdout_tail, "stderr": item.stderr_tail})
        assert secret_value not in stored
        assert token not in stored
        assert "replacement-token" not in stored
        assert "private-key-material" not in stored
        assert "mail body" not in stored
        assert row.params["password"] == "***"
        assert row.changes["api_token"]["before"]["hash"].startswith("sha256:")
        assert row.changes["api_token"]["after"]["hash"].startswith("sha256:")
        assert len((item.stdout_tail or "").encode()) <= audit.MAX_STEP_OUTPUT_BYTES
        assert item.stderr_tail == "[REDACTED MAIL CONTENT]"
        assert item.exit_code == 17 and item.result == "failed"


def test_audit_detail_is_admin_only_and_returns_steps(isolated_db):
    with audit.action_trace("test.detail", {}) as trace:
        with audit.step("detail step"):
            pass
        ident = audit.record("admin", "admin", "test.detail", "demo", {}, "ok", trace=trace)
    customer = sec.Identity(2, "customer", "customer", 1, "session")
    with pytest.raises(HTTPException) as denied:
        auditlog.audit_log_detail(ident, identity=customer)
    assert denied.value.status_code == 403
    admin = sec.Identity(1, "admin", "admin", None, "session")
    detail = auditlog.audit_log_detail(ident, identity=admin)
    assert detail["id"] == ident
    assert [item["name"] for item in detail["steps"]] == ["detail step"]


def test_audit_csv_keeps_old_columns_and_appends_p3_fields(isolated_db):
    audit.record("admin", "admin", "test.csv", "demo", {}, "ok",
                 source_ip="203.0.113.5", duration_ms=10,
                 changes={"enabled": {"before": False, "after": True}})
    identity = sec.Identity(1, "admin", "admin", None, "session")
    response = auditlog.export_csv(identity=identity)

    async def collect():
        return "".join([chunk async for chunk in response.body_iterator])

    header = next(__import__("csv").reader(io.StringIO(asyncio.run(collect()))))
    assert header[:9] == ["id", "created_at", "actor", "role", "op", "target", "result", "detail", "request_id"]
    assert header[-3:] == ["source_ip", "duration_ms", "changes"]


def test_additive_p3_migration_is_idempotent_on_legacy_audit_table(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, created_at DATETIME)"
        )
    ActionStep.__table__.create(engine)
    _apply_additive_migrations(engine)
    _apply_additive_migrations(engine)
    inspector = inspect(engine)
    columns = {item["name"] for item in inspector.get_columns("audit_log")}
    assert {"request_id", "source_ip", "duration_ms", "changes"} <= columns
    indexes = {item["name"] for item in inspector.get_indexes("audit_log")}
    assert {"ix_audit_log_request_id", "ix_audit_log_created_at"} <= indexes
    assert "action_steps" in inspector.get_table_names()


def test_retention_clamps_minimums_and_prunes_in_bounded_batches(isolated_db, monkeypatch):
    now = utcnow()
    monkeypatch.setattr(audit.settings, "audit_retention_days", 1)
    monkeypatch.setattr(audit.settings, "audit_step_retention_days", 1)
    with write_session() as db:
        old_ids = []
        for offset in range(5):
            row = AuditLog(
                actor="admin", role="admin", op="old", target=None, result="ok",
                created_at=now - dt.timedelta(days=100 + offset),
            )
            db.add(row)
            db.flush()
            old_ids.append(row.id)
            db.add(ActionStep(
                audit_id=row.id, name="old step", started_at=now - dt.timedelta(days=31),
                duration_ms=1, result="ok",
            ))
        recent = AuditLog(actor="admin", role="admin", op="recent", target=None, result="ok", created_at=now)
        db.add(recent)
        db.flush()
        recent_id = recent.id
        db.add(ActionStep(audit_id=recent_id, name="recent step", started_at=now, duration_ms=1, result="ok"))
        middle = AuditLog(
            actor="admin", role="admin", op="middle", target=None, result="ok",
            created_at=now - dt.timedelta(days=60),
        )
        db.add(middle)
        db.flush()
        middle_id = middle.id
        db.add(ActionStep(
            audit_id=middle_id, name="expired detail", started_at=now - dt.timedelta(days=60),
            duration_ms=1, result="ok",
        ))

    result = audit.prune_retention(now=now, batch_size=2)
    assert result == {
        "action_steps": 6,
        "audit_rows": 5,
        "audit_retention_days": audit.MIN_AUDIT_RETENTION_DAYS,
        "step_retention_days": audit.MIN_STEP_RETENTION_DAYS,
        "batch_size": 2,
    }
    with write_session() as db:
        assert db.get(AuditLog, recent_id) is not None
        assert db.scalar(select(ActionStep).where(ActionStep.audit_id == recent_id)) is not None
        assert db.get(AuditLog, middle_id) is not None
        assert db.scalar(select(ActionStep).where(ActionStep.audit_id == middle_id)) is None
        assert not db.scalars(select(AuditLog).where(AuditLog.id.in_(old_ids))).all()
