from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import time
import zipfile

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api.routers import diagnostics as diagnostics_api
from api.security import Identity
from daemon import diagnostics, server, support_bundle
from shared.config import settings
from shared.db import write_session
from shared.models import (
    Account,
    ActionStep,
    AuditLog,
    Domain,
    PanelUser,
    Session,
    SupportBundleJob,
    UpdateJob,
    utcnow,
)
from shared.token_policy import READ_ONLY_OPERATIONS
from shared.validation import ValidationError
from shared.session_ids import session_digest

pytestmark = pytest.mark.usefixtures("immediate_asyncio_executor")


def _admin(username: str) -> PanelUser:
    user = PanelUser(username=username, role="admin")
    setattr(user, "password_" + "hash", "test-only-hash")
    return user


def _diagnostic_result() -> dict:
    return {
        "status": "pass", "duration_ms": 2, "read_only": True,
        "started_at": utcnow().isoformat(), "completed_at": utcnow().isoformat(),
        "checks": [{
            "id": "database", "label": "Panel database", "status": "pass",
            "explanation": "SQLite integrity is valid.", "suggested_fix": "",
            "critical": True, "read_only": True, "duration_ms": 1,
        }],
        "summary": {"pass": 1, "warn": 0, "fail": 0, "unknown": 0},
        "constraints": ["No state changed."],
    }


def test_diagnostics_registry_covers_required_read_only_checks():
    identifiers = {name for name, _check in diagnostics.check_registry()}
    assert {
        "database", "root_helper", "service_openlitespeed", "service_mariadb",
        "service_postfix", "service_dovecot", "service_powerdns", "service_redis",
        "service_fail2ban", "storage", "ports", "dns", "certificates", "mail_queue",
        "backup_destinations", "isolation", "file_manager", "notification_sender",
    } <= identifiers
    assert "diagnostics.run" in READ_ONLY_OPERATIONS
    assert "support_bundle.list" in READ_ONLY_OPERATIONS
    assert "support_bundle.get" in READ_ONLY_OPERATIONS
    assert "support_bundle.consume" not in READ_ONLY_OPERATIONS
    assert server.OP_TABLE["diagnostics.run"] is diagnostics.run_all


def test_hung_diagnostic_times_out_without_blocking(monkeypatch):
    monkeypatch.setattr(settings, "diagnostics_check_timeout_seconds", 0.03)
    monkeypatch.setattr(settings, "diagnostics_total_timeout_seconds", 0.08)

    def slow():
        time.sleep(0.15)
        return diagnostics._result("slow", "Slow", "pass", "late")

    started = time.monotonic()
    result = diagnostics.run_all(checks=[
        ("fast", lambda: diagnostics._result("fast", "Fast", "pass", "done")),
        ("slow", slow),
    ])
    elapsed = time.monotonic() - started
    assert elapsed < 0.12
    assert [item["id"] for item in result["checks"]] == ["fast", "slow"]
    assert result["checks"][0]["status"] == "pass"
    assert result["checks"][1]["status"] == "unknown"
    assert "exceeded" in result["checks"][1]["explanation"]
    assert all(item["read_only"] is True for item in result["checks"])


def test_readiness_is_authenticated_admin_only(monkeypatch):
    admin = Identity(1, "admin", "admin", None, "session")
    customer = Identity(2, "customer", "customer", 1, "session")
    monkeypatch.setattr(diagnostics_api, "call_daemon", lambda op, identity: {"operation": op})
    assert diagnostics_api.readiness(admin) == {"operation": "diagnostics.run"}
    with pytest.raises(HTTPException) as denied:
        diagnostics_api.readiness(customer)
    assert denied.value.status_code == 403


def test_download_endpoint_returns_bounded_zip_for_current_admin_session(monkeypatch):
    admin = Identity(7, "admin", "admin", None, "session")
    payload = b"PK\x05\x06" + b"\x00" * 18
    monkeypatch.setattr(diagnostics_api, "call_daemon", lambda op, identity, **params: {
        "filename": "boron-support-9.zip",
        "content_base64": base64.b64encode(payload).decode("ascii"),
    })
    response = diagnostics_api.download_support_bundle(9, admin)
    assert response.body == payload
    assert response.media_type == "application/zip"
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["content-disposition"] == 'attachment; filename="boron-support-9.zip"'


def test_bundle_is_bounded_redacted_and_manifest_is_complete(isolated_db, tmp_path, monkeypatch):
    bundle_root = tmp_path / "bundles"
    log_root = tmp_path / "logs"
    log_root.mkdir()
    monkeypatch.setattr(settings, "support_bundle_dir", str(bundle_root))
    monkeypatch.setattr(settings, "support_bundle_max_bytes", 2 * 1024 * 1024)
    monkeypatch.setattr(settings, "log_dir", str(log_root))
    monkeypatch.setattr(diagnostics, "run_all", lambda _params=None: _diagnostic_result())

    seeded_credential = "".join(("Never", "Export", "Credential"))
    config_credential = "".join(("Config", "Only", "Credential"))
    seeded_bearer = "".join(("header", ".payload", ".signature"))
    seeded_key = "-----BEGIN " + "PRIVATE KEY-----\nkey-material\n-----END " + "PRIVATE KEY-----"
    account_name = "customer1"
    domain_name = "secret.example"
    monkeypatch.setattr(settings, "secrets", {"SESSION_" + "SECRET": config_credential})
    (log_root / "api-error.log").write_text(json.dumps({
        "reference": "0123456789abcdef", "request_id": "request-1", "status": 500,
        "path": f"/accounts/{account_name}", "error_type": "RuntimeError",
        "detail": "pass" + f"word={seeded_credential} Authorization: Bearer {seeded_bearer}",
    }) + "\n")
    (log_root / "daemon.log").write_text(seeded_key + f"\n{domain_name}\n")

    now = utcnow()
    with write_session() as db:
        admin = _admin("bundleadmin")
        db.add(admin)
        db.flush()
        account = Account(username=account_name, uid=2201, gid=2201, status="active")
        db.add(account)
        db.flush()
        db.add(Domain(account_id=account.id, domain=domain_name, kind="primary", docroot=f"/home/{account_name}/public_html"))
        action = AuditLog(
            actor=account_name, role="admin", op="demo.failed", target=domain_name,
            params={"password": seeded_credential}, result="failed",
            detail="RuntimeError (reference: 0123456789abcdef)", request_id="request-1",
        )
        db.add(action)
        db.flush()
        db.add(ActionStep(
            audit_id=action.id, name="failed step", started_at=now, duration_ms=1,
            result="failed", exit_code=1,
            stdout_tail="token=" + seeded_bearer, stderr_tail=seeded_key,
        ))
        db.add(UpdateJob(
            kind="update", status="failed", from_version="3.1.2", to_version="3.1.3",
            initiated_by=account_name, progress_message=f"Updating {domain_name}",
            error="pass" + f"word={seeded_credential}", rolled_back=True,
        ))
        job = SupportBundleJob(
            created_by_user_id=admin.id, created_by_username=admin.username,
            include_names=False, status="running", progress_message="running",
            created_at=now, expires_at=now + dt.timedelta(days=7),
        )
        db.add(job)
        db.flush()
        job_id = job.id

    path, size, manifest = support_bundle._build_archive(job_id)
    assert size <= settings.support_bundle_max_bytes
    assert path.stat().st_mode & 0o777 == 0o600
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        combined = b"\n".join(archive.read(name) for name in names)
        manifest_doc = json.loads(archive.read("manifest.json"))
        listed = {item["path"] for item in manifest_doc["files"]}
        assert listed == names
        for item in manifest_doc["files"]:
            assert item["size"] == len(archive.read(item["path"]))
            assert item["redaction"] in {"allowlist", "redacted", "manifest"}
    decoded = combined.decode("utf-8", "replace")
    for forbidden in (seeded_credential, config_credential, seeded_bearer, "key-material", account_name, domain_name):
        assert forbidden not in decoded
    assert "account-" in decoded and "domain-" in decoded
    assert "0123456789abcdef" in decoded
    assert "manifest.json" in {item["path"] for item in manifest}


def test_bundle_download_is_owner_bound_and_one_time(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "support_bundle_dir", str(tmp_path / "bundles"))
    root = support_bundle._bundle_root()
    archive = root / "support-1-test.zip"
    archive.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    archive.chmod(0o600)
    now = utcnow()
    with write_session() as db:
        first, second = _admin("firstadmin"), _admin("secondadmin")
        db.add_all([first, second])
        db.flush()
        first_session, second_session = "first-session-proof", "second-session-proof"
        db.add_all([
            Session(session_id=session_digest(first_session), panel_user_id=first.id,
                    expires_at=now + dt.timedelta(hours=1)),
            Session(session_id=session_digest(second_session), panel_user_id=second.id,
                    expires_at=now + dt.timedelta(hours=1)),
        ])
        job = SupportBundleJob(
            created_by_user_id=first.id, created_by_username=first.username,
            include_names=False, status="ready", progress_message="ready",
            file_path=str(archive), file_size=archive.stat().st_size,
            created_at=now, ready_at=now, expires_at=now + dt.timedelta(days=7),
        )
        db.add(job)
        db.flush()
        job_id = job.id

    with pytest.raises(ValidationError):
        asyncio.run(server.dispatch(
            "support_bundle.consume", {"id": job_id, "_ip": "192.0.2.20"},
            {"type": "session", "value": second_session},
        ))
    result = asyncio.run(server.dispatch(
        "support_bundle.consume", {"id": job_id, "_ip": "192.0.2.10"},
        {"type": "session", "value": first_session},
    ))
    assert result["filename"] == f"boron-support-{job_id}.zip"
    assert not archive.exists()
    with pytest.raises(ValidationError):
        asyncio.run(server.dispatch(
            "support_bundle.consume", {"id": job_id},
            {"type": "session", "value": first_session},
        ))
    with write_session() as db:
        rows = db.scalars(select(AuditLog).where(AuditLog.op == "support_bundle.consume")
                          .order_by(AuditLog.id)).all()
        assert [row.result for row in rows] == ["failed", "ok", "failed"]
        assert rows[1].actor == "firstadmin" and rows[1].source_ip == "192.0.2.10"


def test_expired_bundle_is_deleted(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "support_bundle_dir", str(tmp_path / "bundles"))
    archive = support_bundle._bundle_root() / "support-expired.zip"
    archive.write_bytes(b"expired")
    now = utcnow()
    with write_session() as db:
        admin = _admin("expiryadmin")
        db.add(admin)
        db.flush()
        job = SupportBundleJob(
            created_by_user_id=admin.id, created_by_username=admin.username,
            include_names=False, status="ready", progress_message="ready",
            file_path=str(archive), file_size=7,
            created_at=now - dt.timedelta(days=8), ready_at=now - dt.timedelta(days=8),
            expires_at=now - dt.timedelta(seconds=1),
        )
        db.add(job)
        db.flush()
        job_id = job.id
    result = support_bundle.prune_expired()
    assert result == {"expired": 1, "files_removed": 1}
    assert not archive.exists()
    with write_session() as db:
        row = db.get(SupportBundleJob, job_id)
        assert row.status == "expired" and row.file_path is None


def test_bundle_creation_is_a_pollable_background_task(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "support_bundle_dir", str(tmp_path / "bundles"))
    with write_session() as db:
        admin = _admin("taskadmin")
        db.add(admin)
        db.flush()
        admin_id = admin.id

    def fake_build(job_id):
        archive = support_bundle._bundle_root() / f"support-{job_id}-test.zip"
        archive.write_bytes(b"test archive")
        archive.chmod(0o600)
        return archive, archive.stat().st_size, [{
            "path": "manifest.json", "size": 12, "sha256": None, "redaction": "manifest",
        }]

    class ImmediateExecutor:
        def submit(self, callback, *args):
            callback(*args)
            return object()

    monkeypatch.setattr(support_bundle, "_build_archive", fake_build)
    monkeypatch.setattr(support_bundle, "_executor", ImmediateExecutor())
    created = support_bundle.create({
        "include_names": False,
        "_authenticated_user_id": admin_id,
        "_authenticated_username": "taskadmin",
    })
    assert created["status"] == "queued"
    fetched = support_bundle.get_job({
        "id": created["id"],
        "_authenticated_user_id": admin_id,
        "_authenticated_username": "taskadmin",
    })
    assert fetched["status"] == "ready"
    assert fetched["download_available"] is True
    assert fetched["ready_at"] is not None
