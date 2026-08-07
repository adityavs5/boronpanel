from __future__ import annotations

import json

import pytest

from api import logsetup
from api.security import Identity, get_identity


@pytest.fixture()
def log_dir(tmp_path, monkeypatch):
    """Point the request logs at a throwaway dir and force a reconfigure."""
    monkeypatch.setattr(logsetup.settings, "log_dir", str(tmp_path))
    logsetup.reset()
    yield tmp_path
    logsetup.reset()


# --- record shape ------------------------------------------------------------


def test_build_record_has_all_required_fields():
    rec = logsetup.build_record("GET", "/api/v1/accounts", 200, 12.345, "admin", "203.0.113.5")
    assert set(rec) == {"timestamp", "method", "path", "status", "duration_ms", "user", "ip"}
    assert rec["method"] == "GET"
    assert rec["path"] == "/api/v1/accounts"
    assert rec["status"] == 200
    assert rec["duration_ms"] == 12.3  # rounded to 1dp
    assert rec["user"] == "admin"
    assert rec["ip"] == "203.0.113.5"
    # timestamp is ISO-8601 with a timezone.
    assert rec["timestamp"].endswith("+00:00")


def test_unauthenticated_request_logs_null_user():
    rec = logsetup.build_record("POST", "/login", 401, 3.0, None, "203.0.113.9")
    assert rec["user"] is None


# --- file writing ------------------------------------------------------------


def _read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_access_line_written_for_every_request(log_dir):
    logsetup.record_access("GET", "/healthz", 200, 1.0, None, "203.0.113.1")
    lines = _read_lines(logsetup.access_log_path(str(log_dir)))
    assert len(lines) == 1
    assert lines[0]["path"] == "/healthz"
    assert lines[0]["status"] == 200
    # A 200 must NOT appear in the error log.
    assert not logsetup.error_log_path(str(log_dir)).exists() or _read_lines(logsetup.error_log_path(str(log_dir))) == []


def test_5xx_written_to_both_access_and_error_logs(log_dir):
    logsetup.record_access("GET", "/api/v1/accounts", 503, 8.0, "admin", "203.0.113.2")
    access = _read_lines(logsetup.access_log_path(str(log_dir)))
    errors = _read_lines(logsetup.error_log_path(str(log_dir)))
    assert len(access) == 1 and access[0]["status"] == 503
    assert len(errors) == 1 and errors[0]["status"] == 503


def test_4xx_not_written_to_error_log(log_dir):
    logsetup.record_access("GET", "/api/v1/accounts", 404, 2.0, "admin", "203.0.113.3")
    assert _read_lines(logsetup.error_log_path(str(log_dir))) == []


def test_unwritable_log_dir_does_not_raise(monkeypatch):
    # A path under a file (impossible to mkdir) simulates an unwritable dir.
    monkeypatch.setattr(logsetup.settings, "log_dir", "/proc/1/cmdline/nope")
    logsetup.reset()


def test_existing_directory_chmod_denial_does_not_disable_logging(tmp_path, monkeypatch):
    monkeypatch.setattr(logsetup.settings, "log_dir", str(tmp_path))
    monkeypatch.setattr(logsetup.Path, "chmod", lambda _self, _mode: (_ for _ in ()).throw(PermissionError()))
    logsetup.reset()

    logsetup.record_access("GET", "/healthz", 200, 1.0, None, "203.0.113.4")

    assert logsetup.access_log_path(str(tmp_path)).is_file()
    logsetup.reset()
    # Must not raise -- logging can never take the API down.
    logsetup.record_access("GET", "/healthz", 200, 1.0, None, "203.0.113.4")
    logsetup.reset()


# --- tail reader -------------------------------------------------------------


def test_tail_empty_when_no_error_log(log_dir):
    assert logsetup.tail_error_records(log_dir=str(log_dir)) == []


def test_tail_returns_newest_first_and_respects_limit(log_dir):
    for i in range(10):
        logsetup.record_access("GET", f"/x/{i}", 500, 1.0, None, "203.0.113.5")
    recs = logsetup.tail_error_records(limit=3, log_dir=str(log_dir))
    assert [r["path"] for r in recs] == ["/x/9", "/x/8", "/x/7"]


def test_tail_skips_malformed_lines(log_dir):
    path = logsetup.error_log_path(str(log_dir))
    path.write_text('{"path":"/good1","status":500}\nnot json at all\n{"path":"/good2","status":500}\n')
    recs = logsetup.tail_error_records(log_dir=str(log_dir))
    assert [r["path"] for r in recs] == ["/good2", "/good1"]


def test_tail_drops_partial_first_line_after_seek_cut(log_dir):
    path = logsetup.error_log_path(str(log_dir))
    # A record far larger than the 256KiB tail window forces the seek to
    # land mid-line; that partial leading fragment must be dropped, not
    # returned as a broken record (and must not crash).
    big = '{"path":"/huge","status":500,"pad":"' + ("A" * 300 * 1024) + '"}\n'
    tail = '{"path":"/recent","status":500}\n'
    path.write_text(big + tail)
    recs = logsetup.tail_error_records(log_dir=str(log_dir))
    assert [r["path"] for r in recs] == ["/recent"]


# --- admin endpoint ----------------------------------------------------------


def test_admin_errors_endpoint_returns_recent(log_dir, isolated_db):
    from fastapi.testclient import TestClient

    import api.main as main

    for i in range(3):
        logsetup.record_access("GET", f"/e/{i}", 500, 1.0, "admin", "203.0.113.6")

    admin = Identity(panel_user_id=1, username="adminuser", role="admin", account_id=None, auth_method="session")
    main.app.dependency_overrides[get_identity] = lambda: admin
    try:
        client = TestClient(main.app)
        r = client.get("/api/v1/admin/logs/errors")
        assert r.status_code == 200
        errors = r.json()["errors"]
        assert [e["path"] for e in errors] == ["/e/2", "/e/1", "/e/0"]
    finally:
        main.app.dependency_overrides.pop(get_identity, None)


def test_admin_errors_endpoint_requires_admin(log_dir, isolated_db):
    from fastapi.testclient import TestClient

    import api.main as main

    customer = Identity(panel_user_id=2, username="cust1", role="customer", account_id=1, auth_method="session")
    main.app.dependency_overrides[get_identity] = lambda: customer
    try:
        client = TestClient(main.app)
        r = client.get("/api/v1/admin/logs/errors")
        assert r.status_code == 403
    finally:
        main.app.dependency_overrides.pop(get_identity, None)


def test_admin_errors_endpoint_unauthenticated_rejected(log_dir, isolated_db):
    from fastapi.testclient import TestClient

    import api.main as main

    client = TestClient(main.app)
    r = client.get("/api/v1/admin/logs/errors")
    assert r.status_code == 401


def test_middleware_logs_a_real_request(log_dir, isolated_db):
    """End-to-end: a request through the real ASGI stack writes an access
    line with the true final status."""
    from fastapi.testclient import TestClient

    import api.main as main

    client = TestClient(main.app)
    r = client.get("/healthz")
    assert r.status_code == 200
    lines = _read_lines(logsetup.access_log_path(str(log_dir)))
    assert any(line["path"] == "/healthz" and line["status"] == 200 for line in lines)
