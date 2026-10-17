"""Regression boundaries from the October 17 supplied static review."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from daemon import audit, dnscluster, handlers_auth, notifications
from daemon.rpc_authority import Principal, authorize, AuthorizationError
from shared.db import write_session
from shared.models import PanelUser, Session, ImpersonationSession, utcnow
from shared.passwords import hash_password
from shared.validation import ValidationError


@pytest.mark.parametrize("key", ["license_key", "recovery_key", "ssh_key_passphrase", "access_key", "SECRET_KEY"])
def test_audit_redacts_secrets_in_params_changes_and_output(key):
    value = "only-a-synthetic-secret-76543"
    assert value not in json.dumps(audit._sanitize("", {"nested": [{key: value}]}))
    with audit.action_trace("fixture", {key: value}) as trace:
        audit.change(key, value, value + "new")
        with audit.step("fixture") as step:
            step.stdout = f"{key}={value}"
        assert value not in json.dumps(trace.changes)
        assert value not in trace.steps[0].stdout_tail


@pytest.mark.parametrize("template", [None, "User {{username}} password {{password}}"])
def test_welcome_never_contains_password(template):
    body = notifications._render_body("account.created", "demo", {"initial_password": "never-mail-this"}, template)
    assert "never-mail-this" not in body


@pytest.mark.parametrize("field,value", [
    ("endpoint", "https://other.example"), ("username", "otheradmin"),
    ("peer_type", "cpanel"), ("verify_tls", False),
])
def test_dns_peer_requires_replacement_for_new_trust_target(isolated_db, field, value):
    from shared.models import DnsClusterPeer
    peer = dnscluster.create_peer({"name": "peer", "peer_type": "directadmin",
        "endpoint": "https://dns.example", "username": "admin", "credential": "old-fixture-key"})
    with write_session() as db:
        old = db.get(DnsClusterPeer, peer["id"]).credential_enc
    with pytest.raises(ValidationError, match="replacement credential"):
        dnscluster.update_peer({"peer_id": peer["id"], field: value})
    with write_session() as db:
        assert db.get(DnsClusterPeer, peer["id"]).credential_enc == old
    dnscluster.update_peer({"peer_id": peer["id"], field: value, "credential": "new-fixture-key"})


def test_dns_peer_name_only_preserves_credential(isolated_db):
    peer = dnscluster.create_peer({"name": "peer", "peer_type": "boron", "endpoint": "https://dns.example"})
    assert dnscluster.update_peer({"peer_id": peer["id"], "name": "renamed"})["name"] == "renamed"


@pytest.mark.parametrize("op", ["wordpress.install.get", "apps.install.get", "wpcli.get", "composer.get",
                               "cpanel_import.get", "portable.import.get"])
def test_readonly_token_cannot_consume_credentials(isolated_db, op):
    principal = Principal("admin", "monitor", None, None, "token", token_scope="read-only")
    with pytest.raises(AuthorizationError):
        authorize(op, {"username": "demo", "job_id": 1}, principal)


@pytest.mark.parametrize("op", ["totp.setup", "totp.verify"])
def test_totp_enrollment_requires_password_at_root_boundary(isolated_db, op):
    with write_session() as db:
        user = PanelUser(username="securityadmin", role="admin", password_hash=hash_password("Fixture-pass-928!"))
        db.add(user); db.flush(); uid = user.id
    principal = Principal("admin", "securityadmin", None, uid, "session")
    for password in (None, "", "wrong"):
        with pytest.raises(AuthorizationError):
            authorize(op, {"panel_user_id": uid, "current_password": password}, principal)
    authorize(op, {"panel_user_id": uid, "current_password": "Fixture-pass-928!"}, principal)


def _request(headers):
    return Request({"type": "http", "method": "GET", "scheme": "https", "server": ("panel.example", 2222),
                    "path": "/api/v1/jobs/1", "headers": [(b"host", b"panel.example:2222")] +
                    [(k.encode(), v.encode()) for k, v in headers.items()]})


@pytest.mark.parametrize("headers", [{}, {"sec-fetch-site": "cross-site"},
    {"sec-fetch-site": "same-site"}, {"referer": "https://tenant.example/x"},
    {"origin": "https://other.example", "sec-fetch-site": "same-origin"}])
def test_credential_get_rejects_cross_site_navigation(headers):
    from api.security import require_credential_origin
    with pytest.raises(HTTPException) as error:
        require_credential_origin(_request(headers), SimpleNamespace(auth_method="session"))
    assert error.value.status_code == 403


@pytest.mark.parametrize("headers", [{"sec-fetch-site": "same-origin"}, {"referer": "https://panel.example:2222/app"}])
def test_credential_get_preserves_panel_polling(headers):
    from api.security import require_credential_origin
    require_credential_origin(_request(headers), SimpleNamespace(auth_method="session"))


def test_file_stream_limits_release_and_reject_without_queue(monkeypatch):
    from api.routers import filebrowser as fb
    monkeypatch.setattr(fb, "_active_streams", {})
    monkeypatch.setattr(fb, "MAX_GLOBAL_STREAMS", 3)
    monkeypatch.setattr(fb, "MAX_ACCOUNT_STREAMS", 2)
    leases = [fb._StreamLease("a"), fb._StreamLease("a")]
    with pytest.raises(HTTPException):
        fb._StreamLease("a")
    leases.append(fb._StreamLease("b"))
    with pytest.raises(HTTPException):
        fb._StreamLease("c")
    for lease in leases: lease.release(); lease.release()
    assert fb._active_streams == {}


def test_stream_response_failure_closes_client_and_releases_slot(monkeypatch):
    from api.routers import filebrowser as fb
    monkeypatch.setattr(fb, "_active_streams", {})
    closed = []
    class Response:
        status_code = 200
        raw_headers = []
        async def __call__(self, *args): raise RuntimeError("disconnected")
    class Client:
        async def aclose(self): closed.append(True)
    response = fb._LeasedResponse(Response(), Client(), fb._StreamLease("a"))
    with pytest.raises(RuntimeError, match="disconnected"):
        asyncio.run(response({}, None, None))
    assert closed == [True] and fb._active_streams == {}


def test_file_upload_byte_limit(monkeypatch):
    from api.routers import filebrowser as fb
    monkeypatch.setattr(fb, "MAX_UPLOAD_BYTES", 3)
    class Req:
        async def stream(self):
            yield b"12"
            yield b"34"
    async def read():
        return [chunk async for chunk in fb._bounded_upload(Req())]
    with pytest.raises(HTTPException) as exc: asyncio.run(read())
    assert exc.value.status_code == 413


def test_install_mysql_password_not_in_command_arguments():
    source = Path("scripts/install.sh").read_text()
    assert 'mysql -u root -p' not in source
    assert 'mysql --defaults-file=/root/.my.cnf boron_mail' in source


def test_local_backup_refuses_writable_ancestor_and_existing_symlink(tmp_path):
    from daemon import backup
    parent = tmp_path / "mutable"
    parent.mkdir()
    destination = parent / "backups"; destination.mkdir()
    parent.chmod(0o777)
    with pytest.raises(ValidationError):
        with backup._local_directory(destination): pass
    parent.chmod(0o700)
    source = tmp_path / "source"; source.write_bytes(b"backup")
    victim = tmp_path / "victim"; victim.write_bytes(b"safe")
    (destination / "archive").symlink_to(victim)
    with pytest.raises(FileExistsError):
        backup._publish_local_artifact(destination, "archive", source)
    assert victim.read_bytes() == b"safe"
    result = backup._publish_local_artifact(destination, "good", source)
    assert Path(result).read_bytes() == b"backup"
    assert not source.exists()


@pytest.mark.parametrize("bulk", [False, True])
def test_admin_can_revoke_owned_impersonation_without_parent(bulk, isolated_db):
    import datetime as dt
    from shared.models import Account
    with write_session() as db:
        user = PanelUser(username="owner", role="admin", password_hash="unused")
        account = Account(username="demo", status="active")
        db.add_all([user, account]); db.flush()
        parent = Session(session_id="a" * 64, panel_user_id=user.id, expires_at=utcnow() + dt.timedelta(hours=1))
        child = Session(session_id="b" * 64, panel_user_id=user.id, expires_at=utcnow() + dt.timedelta(hours=1))
        db.add_all([parent, child]); db.flush()
        imp = ImpersonationSession(session_id=child.session_id, account_id=account.id,
                                   admin_panel_user_id=user.id, admin_username="owner")
        db.add(imp); db.flush()
        uid, child_id, parent_id, imp_id = user.id, child.id, parent.id, imp.id
    params = {"panel_user_id": uid, "_current_session_hash": "a" * 64, "session_row_id": child_id}
    listing = handlers_auth.list_sessions(params)
    assert listing["impersonation_sessions"][0]["revocable"]
    (handlers_auth.revoke_other_sessions if bulk else handlers_auth.revoke_managed_session)(params)
    with write_session() as db:
        assert db.get(Session, child_id).revoked
        assert not db.get(Session, parent_id).revoked
        assert db.get(ImpersonationSession, imp_id).ended_at is not None


def test_export_uses_shared_protected_handoff_and_cleanup(isolated_db, tmp_path, monkeypatch):
    from daemon import handlers_database as hd, snapshot_databases
    root = tmp_path / "exports"; root.mkdir(mode=0o750)
    monkeypatch.setattr(hd, "_export_directory", lambda: (root, 0))
    monkeypatch.setattr(hd, "_owned_database", lambda *args: (1, 1, "demo_db"))
    monkeypatch.setattr(hd.shutil, "disk_usage", lambda _: SimpleNamespace(free=20 * 1024**3))
    def dump(name, target, work, *, max_bytes):
        assert max_bytes == hd.EXPORT_MAX_BYTES
        target.write_bytes(b"SELECT 1;")
    monkeypatch.setattr(snapshot_databases, "dump_database", dump)
    result = hd.export_database({"username": "demo", "name": "db"})
    target = Path(result["path"])
    assert target.parent.parent == root
    assert target.read_bytes() == b"SELECT 1;"
    assert target.stat().st_mode & 0o777 == 0o640
    assert target.parent.stat().st_mode & 0o777 == 0o750
    with pytest.raises(ValidationError):
        hd.cleanup_database_export({"username": "other", "export_id": result["export_id"]})
    hd.cleanup_database_export({"username": "demo", "export_id": result["export_id"]})
    assert not target.exists()


def test_export_expiry_and_reservation_limit(isolated_db, tmp_path, monkeypatch):
    import os
    from daemon import handlers_database as hd
    root = tmp_path / "exports"; root.mkdir()
    monkeypatch.setattr(hd, "_export_directory", lambda: (root, 0))
    old = root / ("demo-" + "a" * 32); old.mkdir()
    os.utime(old, (0, 0))
    hd.cleanup_database_exports()
    assert not old.exists()
    for char in ("a", "b"):
        (root / ("demo-" + char * 32)).mkdir()
    monkeypatch.setattr(hd, "_owned_database", lambda *args: (1, 1, "demo_db"))
    with pytest.raises(ValidationError, match="Finish"):
        hd.export_database({"username": "demo", "name": "db"})


def test_startup_policy_failure_quarantines_account(isolated_db, tmp_path, monkeypatch):
    from daemon import cgroups, ols, sysops
    from shared.models import Account
    with write_session() as db:
        account = Account(username="demo", uid=2001, status="active")
        db.add(account); db.flush(); account_id = account.id
    monkeypatch.setattr(cgroups, "_cgroup_path", lambda *args: tmp_path)
    monkeypatch.setattr(cgroups, "_stop_account_workloads", lambda _: None)
    monkeypatch.setattr(cgroups, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    monkeypatch.setattr(sysops, "lock_user", lambda _: None)
    stopped = []
    monkeypatch.setattr(ols, "suspend_vhost", lambda account: stopped.append(account.id))
    cgroups.quarantine_failed_policy(account)
    assert (tmp_path / "pids.max").read_text() == "0"
    assert (tmp_path / "cgroup.kill").read_text() == "1"
    with write_session() as db:
        assert db.get(Account, account_id).status == "suspended"
    assert stopped == [account_id]


def test_historical_audit_secrets_are_redacted_on_read_and_storage(isolated_db):
    from api.routers.auditlog import _row_dict
    from shared.models import AuditLog, ActionStep
    with write_session() as db:
        row = AuditLog(actor="admin", role="admin", op="fixture", result="ok",
                       params={"license_key": "historical-fixture-secret"},
                       detail="license_key=historical-fixture-secret")
        db.add(row); db.flush(); row_id = row.id
        db.add(ActionStep(audit_id=row_id, name="write", started_at=utcnow(), duration_ms=1,
                          result="ok", stdout_tail="historical-fixture-secret"))
        assert "historical-fixture-secret" not in json.dumps(_row_dict(row))
    assert audit.redact_historical_batch() == row_id
    assert audit.redact_historical_batch(row_id) is None
    with write_session() as db:
        row = db.get(AuditLog, row_id)
        assert row.params["license_key"] == "***"
        assert "historical-fixture-secret" not in row.detail
        step = db.query(ActionStep).filter_by(audit_id=row_id).one()
        assert "historical-fixture-secret" not in step.stdout_tail


@pytest.mark.parametrize("value", ['{"license_key": "fixture-secret"}',
    "ssh_key_passphrase: a phrase with spaces", "recovery_key='quoted secret'"])
def test_quoted_historical_diagnostics_are_redacted(value):
    from shared.audit_redaction import sanitize_output
    assert "fixture-secret" not in sanitize_output(value)
    assert sanitize_output(value) == "[REDACTED sensitive diagnostic]"
    assert audit._bounded_tail(value, set()) == "[REDACTED sensitive diagnostic]"


def test_startup_quarantine_attempts_all_barriers_if_one_fails(isolated_db, tmp_path, monkeypatch):
    from daemon import cgroups, sysops, ols
    from shared.models import Account
    with write_session() as db:
        account = Account(username="demo", uid=2001, status="active")
        db.add(account); db.flush()
    calls = []
    def lock(_): raise RuntimeError("lock failed")
    monkeypatch.setattr(sysops, "lock_user", lock)
    monkeypatch.setattr(cgroups, "_cgroup_path", lambda *args: tmp_path)
    monkeypatch.setattr(cgroups, "run", lambda *args, **kwargs: calls.append("expiry") or SimpleNamespace(returncode=0))
    monkeypatch.setattr(cgroups, "_stop_account_workloads", lambda _: calls.append("apps"))
    monkeypatch.setattr(ols, "suspend_vhost", lambda _: calls.append("web"))
    with pytest.raises(cgroups.CgroupError, match="login lock"):
        cgroups.quarantine_failed_policy(account)
    assert calls == ["expiry", "web", "apps"]
    assert (tmp_path / "pids.max").read_text() == "0"


@pytest.mark.parametrize("module,router_name,path", [
    ("wordpress", "api_router", "/api/v1/accounts/demo/domains/example.test/wordpress/jobs/1"),
    ("apps", "domain_api_router", "/api/v1/accounts/demo/domains/example.test/apps/jobs/1"),
    ("devtools", "wpcli_router", "/api/v1/accounts/demo/wordpress/wpcli/runs/1"),
])
def test_secret_routes_reject_cross_site_before_rpc(module, router_name, path, monkeypatch):
    import importlib
    from fastapi import FastAPI
    from api.security import Identity, get_identity
    from testsupport.sync_asgi import SyncASGIClient
    router = importlib.import_module("api.routers." + module)
    calls = []
    monkeypatch.setattr(router, "call_daemon", lambda *args, **kwargs: calls.append(args[0]) or {})
    app = FastAPI()
    app.include_router(getattr(router, router_name))
    app.dependency_overrides[get_identity] = lambda: Identity(1, "admin", "admin", None, "session")
    with SyncASGIClient(app, base_url="https://panel.example:2222") as client:
        reply = client.get(path, headers={"sec-fetch-site": "cross-site", "referer": "https://hostile.example"})
    assert reply.status_code == 403
    assert calls == []
