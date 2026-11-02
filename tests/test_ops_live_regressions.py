"""Regressions for the backup/admin/files findings of the live demo tests."""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import json
import os
import pwd as real_pwd
import tarfile
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api.routers import backups as backups_router
from api.routers import filebrowser as fbr
from api.routers import sshkeys as sshkeys_router
from api.routers import tokens as tokens_router
from api.security import Identity
from daemon import backup, cron, firewall, ipban, portable_archive, snapshot_jobs, sshkeys
from daemon import impersonation
from daemon.procutil import ProcResult
from daemon.rpc_policy import POLICY_BY_OPERATION
from shared.db import write_session
from shared.models import (
    Account, AuditLog, BackupDestination, BackupJob, BackupSchedule, FirewallTemporaryBan,
    ImpersonationSession, ImpersonationToken, PanelUser, PermanentIpBan, Session, SnapshotDestination,
    SnapshotPolicy, SnapshotRun, utcnow,
)
from shared.validation import ValidationError


def _ok(args, **_kwargs):
    return ProcResult(args, 0, "", "")


# --- firewall / IP bans -----------------------------------------------------


def test_temporary_ban_delete_handles_naive_sqlite_expiry(isolated_db, monkeypatch):
    """SQLite returns DateTime(timezone=True) values naive: the restore
    duration must not raise TypeError (it did on every delete)."""
    with write_session() as session:
        session.add(FirewallTemporaryBan(value="203.0.113.7", banned_by="admin",
                                         expires_at=(utcnow() + dt.timedelta(minutes=30)).replace(tzinfo=None)))
    with write_session() as session:
        ban_id = session.scalar(select(FirewallTemporaryBan.id))
    captured = {}

    def fake_start(operation, summary, undo, apply):
        captured["undo"] = undo
        return {"operation": operation}

    monkeypatch.setattr(firewall, "_start_change", fake_start)
    assert firewall.transactional_delete_temporary_ban({"id": ban_id})["operation"] == "temporary_ban.delete"
    assert 29 <= captured["undo"][0]["params"]["duration_minutes"] <= 31


@pytest.mark.parametrize("value", [0, "0", -5, True, "abc"])
def test_temporary_ban_duration_zero_is_rejected_not_defaulted(isolated_db, monkeypatch, value):
    monkeypatch.setattr(firewall, "run", lambda args, timeout=20: pytest.fail("must not reach ufw"))
    with pytest.raises(ValidationError, match="duration_minutes"):
        firewall.add_temporary_ban({"value": "203.0.113.8", "duration_minutes": value})


def test_temporary_ban_duration_defaults_only_when_omitted(isolated_db, monkeypatch):
    monkeypatch.setattr(firewall, "assert_address_can_be_banned", lambda value, actor_ip=None: None)
    monkeypatch.setattr(firewall, "run", _ok)
    row = firewall.add_temporary_ban({"value": "203.0.113.9"})
    expires = dt.datetime.fromisoformat(row["expires_at"])
    created = dt.datetime.fromisoformat(row["created_at"])
    assert dt.timedelta(minutes=59) < expires - created <= dt.timedelta(minutes=61)


def test_permanent_unban_keeps_rule_while_temporary_ban_is_active(isolated_db, monkeypatch):
    calls = []
    monkeypatch.setattr(ipban, "run", lambda args, timeout=20: calls.append(args) or _ok(args))
    with write_session() as session:
        session.add(FirewallTemporaryBan(value="203.0.113.77", banned_by="admin",
                                         expires_at=utcnow() + dt.timedelta(minutes=30)))
        session.add(PermanentIpBan(value="203.0.113.77", banned_by="admin"))
    with write_session() as session:
        ban_id = session.scalar(select(PermanentIpBan.id))
    assert ipban.unban_ip({"id": ban_id})["status"] == "unbanned"
    assert not any("delete" in call for call in calls)
    with write_session() as session:
        assert session.scalar(select(PermanentIpBan.id)) is None


def test_temporary_unban_keeps_rule_of_permanent_ban_and_removes_it_otherwise(isolated_db, monkeypatch):
    calls = []
    monkeypatch.setattr(firewall, "run", lambda args, timeout=20: calls.append(args) or _ok(args))
    with write_session() as session:
        first = FirewallTemporaryBan(value="203.0.113.78", banned_by="admin",
                                     expires_at=utcnow() + dt.timedelta(minutes=30))
        second = FirewallTemporaryBan(value="203.0.113.79", banned_by="admin",
                                      expires_at=utcnow() + dt.timedelta(minutes=30))
        session.add_all([first, second, PermanentIpBan(value="203.0.113.78", banned_by="admin")])
        session.flush()
        shared_id, own_id = first.id, second.id
    firewall.delete_temporary_ban({"id": shared_id})
    assert calls == []
    firewall.delete_temporary_ban({"id": own_id})
    # Comment-less: also removes an uncommented rule a permanent ban created
    # first, which this ban's own insert was skipped against.
    assert calls == [["ufw", "--force", "delete", "deny", "from", "203.0.113.79"]]


def test_firewall_auto_rollback_is_audited(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(firewall.settings, "firewall_state_dir", str(tmp_path / "firewall"))
    monkeypatch.setattr(firewall, "_undo_one", lambda step: None)
    state = {"change_id": "abc123", "operation": "rule.add", "summary": "Add rule", "undo": [{"kind": "x"}],
             "expires_at": 0}
    firewall._write_pending(state)
    firewall._auto_rollback()
    with write_session() as session:
        row = session.scalar(select(AuditLog).where(AuditLog.op == "firewall.auto_rollback"))
        assert row is not None and row.result == "ok" and row.target == "abc123"
        assert row.params["reason"] == "confirmation window expired"
    assert firewall._read_pending() is None


def test_explicit_rollback_is_not_double_audited(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(firewall.settings, "firewall_state_dir", str(tmp_path / "firewall"))
    monkeypatch.setattr(firewall, "_undo_one", lambda step: None)
    firewall._write_pending({"change_id": "def456", "operation": "rule.add", "summary": "x", "undo": [],
                             "expires_at": 0})
    firewall.rollback_change({})
    with write_session() as session:
        assert session.scalar(select(AuditLog.id).where(AuditLog.op == "firewall.auto_rollback")) is None


# --- API tokens / impersonation -----------------------------------------------


def _identity(auth_method: str, **overrides) -> Identity:
    values = dict(panel_user_id=1 if auth_method == "session" else 0, username="admin", role="admin",
                  account_id=None, auth_method=auth_method)
    values.update(overrides)
    return Identity(**values)


@pytest.mark.parametrize("call", [
    lambda identity: tokens_router.create_token(tokens_router.CreateTokenBody(label="chain"), identity),
    lambda identity: tokens_router.revoke_token(1, identity),
])
def test_bearer_tokens_cannot_mint_or_revoke_tokens(monkeypatch, call):
    monkeypatch.setattr(tokens_router, "call_daemon", lambda *a, **k: pytest.fail("must not reach borond"))
    with pytest.raises(HTTPException) as caught:
        call(_identity("token"))
    assert caught.value.status_code == 403


def test_admin_session_can_still_mint_tokens(monkeypatch):
    seen = []
    monkeypatch.setattr(tokens_router, "call_daemon", lambda op, identity, **kw: seen.append(op) or {"id": 1})
    tokens_router.create_token(tokens_router.CreateTokenBody(label="ci"), _identity("session"))
    assert seen == ["auth.create_api_token"]


def test_daemon_refuses_token_minting_without_session_creator(isolated_db):
    from daemon import handlers_auth

    with pytest.raises(ValidationError, match="administrator session"):
        handlers_auth.create_api_token({"label": "x", "role": "admin", "_creator_panel_user_id": None})


def test_terminate_ends_impersonation_sessions_and_pending_tokens(isolated_db):
    with write_session() as session:
        account = Account(username="imp1", status="terminated")
        admin = PanelUser(username="boss", role="admin", password_hash="x")
        session.add_all([account, admin])
        session.flush()
        session.add(Session(session_id="d" * 64, panel_user_id=admin.id, expires_at=utcnow() + dt.timedelta(hours=2)))
        session.add(ImpersonationSession(session_id="d" * 64, account_id=account.id,
                                         admin_panel_user_id=admin.id, admin_username="boss"))
        session.add(ImpersonationToken(token_hash="e" * 64, account_id=account.id, admin_username="boss",
                                       expires_at=utcnow() + dt.timedelta(minutes=5)))
        session.flush()
        assert impersonation.end_account_sessions(session, account.id) == 1
    with write_session() as session:
        assert session.scalar(select(ImpersonationSession)).ended_at is not None
        assert session.scalar(select(Session)).revoked is True
        assert session.scalar(select(ImpersonationToken)).used_at is not None


# --- cron ----------------------------------------------------------------------


def test_cron_percent_is_escaped_for_crontab_and_shown_unescaped(monkeypatch):
    written = {}
    monkeypatch.setattr(cron, "_read_raw", lambda username: [])
    monkeypatch.setattr(cron, "_write_raw", lambda username, lines: written.setdefault("lines", list(lines)))
    job = cron.add_job("demo1", "* * * * *", r"date +%s >> ~/t.log; echo 100\% done")
    line = written["lines"][-1]
    assert line == r"* * * * * date +\%s >> ~/t.log; echo 100\\% done"
    assert job["command"] == r"date +%s >> ~/t.log; echo 100\% done"
    listed = cron.parse_jobs(written["lines"])
    assert listed[0]["command"] == job["command"]


# --- SSH keys --------------------------------------------------------------------


@pytest.fixture()
def ssh_account(isolated_db, tmp_path, monkeypatch):
    home_base = tmp_path / "home"
    (home_base / "demo1").mkdir(parents=True)
    monkeypatch.setattr(sshkeys.settings, "home_base", str(home_base))
    monkeypatch.setattr(sshkeys.sysops, "get_shell", lambda username: sshkeys.sysops.LOGIN_SHELL)
    monkeypatch.setattr(sshkeys.sysops, "set_shell", lambda username, shell: None)
    fake_pw = real_pwd.struct_passwd(("demo1", "x", os.getuid(), os.getgid(), "", str(home_base / "demo1"),
                                      sshkeys.sysops.LOGIN_SHELL))
    monkeypatch.setattr(sshkeys.pwd, "getpwnam", lambda name: fake_pw)
    monkeypatch.setattr(sshkeys.resource_manager, "apply_account", lambda account_id: None)

    def fake_inspect(text, *, timeout=10):
        blob = next(part for part in text.split() if part.startswith("AAAA"))
        return {"bits": "256", "fingerprint": "SHA256:" + blob[-8:], "comment": "", "type": "ED25519"}

    monkeypatch.setattr(sshkeys, "_inspect", fake_inspect)
    with write_session() as session:
        session.add(Account(username="demo1", status="active", uid=5001, gid=5001))
    return home_base / "demo1"


KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKeyMaterial0123456789abcdefghijklmnop qa"


@pytest.mark.parametrize("prefix", ['command="/bin/sh" ', 'from="*" ', 'no-pty,environment="X=1" '])
def test_ssh_key_options_are_rejected(ssh_account, prefix):
    with pytest.raises(ValidationError, match="options"):
        sshkeys.add_key({"username": "demo1", "key": prefix + KEY})
    assert not (ssh_account / ".ssh" / "authorized_keys").exists()


def test_ssh_key_duplicate_is_found_by_key_material(ssh_account):
    (ssh_account / ".ssh").mkdir(mode=0o700)
    (ssh_account / ".ssh" / "authorized_keys").write_text('command="/bin/echo FORCED" ' + KEY + "\n")
    with pytest.raises(ValidationError, match="already added"):
        sshkeys.add_key({"username": "demo1", "key": KEY.rsplit(" ", 1)[0] + " other-comment"})


def test_ssh_key_delete_route_accepts_fingerprints_with_slashes():
    route = next(r for r in sshkeys_router.api_router.routes if "DELETE" in r.methods)
    match, scope = route.matches({"type": "http", "method": "DELETE",
                                  "path": "/api/v1/accounts/demo1/ssh-keys/SHA256:Ej0QFg/3Jx+abc"})
    assert match.name == "FULL"
    assert scope["path_params"]["fingerprint"] == "SHA256:Ej0QFg/3Jx+abc"


# --- WordPress / Joomla --------------------------------------------------------------


@pytest.fixture()
def wp_account(isolated_db, tmp_path, monkeypatch):
    from daemon import wordpress as wp
    from shared.models import Domain

    docroot = tmp_path / "public_html"
    docroot.mkdir()
    with write_session() as session:
        account = Account(username="demo1", status="active", uid=5001, gid=5001)
        session.add(account)
        session.flush()
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot=str(docroot)))
    monkeypatch.setattr(wp._executor, "submit", lambda *a, **k: pytest.fail("an invalid install must not be queued"))
    return wp


@pytest.mark.parametrize("extra,match", [
    ({"path": "../../etc"}, "outside"),
    ({"admin_password": "123"}, "12"),
    ({"admin_email": "not-an-email"}, "email"),
    ({"protocol": "gopher"}, "HTTP"),
    ({"admin_user": "bad\nname"}, "username"),
])
def test_wordpress_install_input_is_rejected_before_queueing(wp_account, extra, match):
    from daemon.wordpress import WordPressJob

    with pytest.raises(ValidationError, match=match):
        wp_account.trigger_install({"username": "demo1", "domain": "demo1.example", **extra})
    with write_session() as session:
        assert session.scalar(select(WordPressJob.id)) is None


def test_joomla_configuration_defines_error_reporting(tmp_path, monkeypatch):
    from daemon import appinstaller

    written = {}
    monkeypatch.setattr(appinstaller, "secure_replace_file",
                        lambda docroot, name, content, uid, gid, mode: written.update(content=content.decode()))
    appinstaller._write_joomla_configuration(str(tmp_path), "db", "user", "pw", "jm_", "Site")
    assert "public $error_reporting = 'none';" in written["content"]
    assert "public $debug = false;" in written["content"]


# --- backups: selective restore, reactivation, policies -----------------------------


def _tar_gz(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.fixture()
def full_artifact(tmp_path):
    content = tmp_path / "build" / "job-7"
    (content / "databases").mkdir(parents=True)
    (content / "mail").mkdir()
    (content / "manifest.json").write_text(json.dumps({"username": "demo1"}))
    (content / "home.tar.gz").write_bytes(_tar_gz({
        "demo1/public_html/index.html": b"original", "demo1/public_html/other.txt": b"x",
        "demo1/private.txt": b"secret",
    }))
    (content / "databases" / "demo1_wp.sql.gz").write_bytes(b"dump")
    (content / "mail" / "demo1.example.tar.gz").write_bytes(_tar_gz({
        "demo1.example/info/cur/1": b"mail", "demo1.example/sales/cur/1": b"other",
    }))
    artifact = tmp_path / "job-7.tar"
    with tarfile.open(artifact, "w") as archive:
        archive.add(content, arcname="job-7")
    work = tmp_path / "work"
    work.mkdir()
    return str(artifact), str(work)


def test_file_restore_from_full_backup_restores_only_the_item(isolated_db, tmp_path, full_artifact, monkeypatch):
    artifact, work = full_artifact
    home_base = tmp_path / "home"
    (home_base / "demo1").mkdir(parents=True)
    monkeypatch.setattr(backup.settings, "home_base", str(home_base))
    monkeypatch.setattr(backup, "_update_restore", lambda *a, **k: None)
    owner = real_pwd.struct_passwd(("demo1", "x", 5001, 5001, "", str(home_base / "demo1"), "/bin/false"))
    monkeypatch.setattr(backup, "_pwnam", lambda name: owner)
    argv = []
    monkeypatch.setattr(backup, "run", lambda args, **kwargs: argv.append(args) or _ok(args))

    backup._restore_item_from_full("demo1", "file", "public_html/index.html", artifact, work, 1)
    assert argv[-1][-4:] == ["--strip-components=1", "--no-wildcards", "--", "demo1/public_html/index.html"]
    assert argv[-1][argv[-1].index("-C") + 1] == str(home_base / "demo1")
    assert not (home_base / "demo1" / "job-7").exists()

    (tmp_path / "work2").mkdir()
    with pytest.raises(ValidationError, match="not in this backup"):
        backup._restore_item_from_full("demo1", "file", "public_html/missing.html", artifact,
                                       str(tmp_path / "work2"), 1)


def test_database_and_mailbox_restore_from_full_backup_use_their_component(isolated_db, tmp_path, full_artifact,
                                                                          monkeypatch):
    artifact, work = full_artifact
    monkeypatch.setattr(backup, "_update_restore", lambda *a, **k: None)
    restored = []
    monkeypatch.setattr(backup, "_restore_database_dump", lambda name, path: restored.append((name, path.name)))
    backup._restore_item_from_full("demo1", "database", "demo1_wp", artifact, work, 1)
    assert restored == [("demo1_wp", "demo1_wp.sql.gz")]

    mail_base = tmp_path / "mail"
    (mail_base / "demo1.example").mkdir(parents=True)
    monkeypatch.setattr(backup.settings, "mail_base", str(mail_base))
    trees = []
    monkeypatch.setattr(backup, "_pwnam", lambda name: "vmail-pw")
    monkeypatch.setattr(backup, "_restore_tree", lambda *a, **k: trees.append((a, k)))
    (tmp_path / "w2").mkdir()
    backup._restore_item_from_full("demo1", "mailbox", "info@demo1.example", artifact, str(tmp_path / "w2"), 1)
    (archive, destination, owner), options = trees[0]
    assert Path(archive).name == "demo1.example.tar.gz"
    assert destination == mail_base / "demo1.example"
    assert options == {"prefix": "demo1.example", "select": "info"}


def test_run_restore_job_routes_item_kinds_of_full_backups_to_selective_restore(isolated_db, tmp_path, monkeypatch):
    with write_session() as session:
        account = Account(username="demo1", status="active", uid=5001, gid=5001)
        destination = BackupDestination(name="d", kind="local", local_path=str(tmp_path))
        session.add_all([account, destination])
        session.flush()
        job = BackupJob(account_id=account.id, kind="full", destination_id=destination.id, status="completed",
                        artifact_path=str(tmp_path / "a.tar"))
        session.add(job)
        session.flush()
        from shared.models import RestoreJob
        restore = RestoreJob(backup_job_id=job.id, account_id=account.id, kind="file",
                             item_ref="public_html/index.html", status="pending")
        session.add(restore)
        session.flush()
        restore_id = restore.id
    monkeypatch.setattr(backup.settings, "backup_staging_dir", str(tmp_path))
    monkeypatch.setattr(backup, "_fetch_artifact_locally", lambda path, kind, tmp: path)
    calls = []
    monkeypatch.setattr(backup, "_restore_item_from_full", lambda *a: calls.append(a))
    monkeypatch.setattr(backup, "_restore_file", lambda *a: pytest.fail("whole-archive file restore"))
    backup._run_restore_job(restore_id)
    assert calls and calls[0][1:3] == ("file", "public_html/index.html")


def test_reactivation_for_restore_clears_stale_uid_and_removes_orphan_home(isolated_db, tmp_path, monkeypatch):
    from daemon import handlers_account, sysops

    with write_session() as session:
        session.add(Account(username="demo1", status="terminated", uid=1015, gid=1015))
    home_base = tmp_path / "home"
    home_base.mkdir()
    monkeypatch.setattr(backup.settings, "home_base", str(home_base))
    seen = {}

    def failing_reactivate(params):
        with write_session() as session:
            seen["uid"] = session.scalar(select(Account.uid).where(Account.username == "demo1"))
        (home_base / "demo1").mkdir()  # useradd --create-home, then rollback
        raise RuntimeError("cgroup setup failed")

    removed = []
    monkeypatch.setattr(handlers_account, "reactivate_account", failing_reactivate)
    monkeypatch.setattr(sysops, "user_exists", lambda username: False)
    monkeypatch.setattr(sysops, "delete_linux_user", lambda username, remove_home=True: removed.append(remove_home))
    with pytest.raises(RuntimeError):
        backup._reactivate_for_restore("demo1", {"username": "demo1"})
    assert seen["uid"] is None
    assert removed == [True]
    # The released identity stays recorded for a retry's ownership remap.
    with write_session() as session:
        row = session.scalar(select(Account).where(Account.username == "demo1"))
        assert (row.uid, row.gid) == (1015, 1015)


def test_reactivation_for_restore_remaps_surviving_home_to_new_uid(isolated_db, tmp_path, monkeypatch):
    from daemon import handlers_account

    with write_session() as session:
        session.add(Account(username="demo1", status="error", uid=1015, gid=1016))
    home_base = tmp_path / "home"
    (home_base / "demo1").mkdir(parents=True)
    monkeypatch.setattr(backup.settings, "home_base", str(home_base))
    monkeypatch.setattr(handlers_account, "reactivate_account", lambda params: {"initial_password": "p"})
    monkeypatch.setattr(backup, "_pwnam", lambda name: real_pwd.struct_passwd(
        ("demo1", "x", 1023, 1024, "", str(home_base / "demo1"), "/bin/false")))
    commands = []
    monkeypatch.setattr(backup, "run", lambda args, **kwargs: commands.append(args) or _ok(args))
    backup._reactivate_for_restore("demo1", {"username": "demo1"})
    assert commands == [
        ["chown", "-R", "--no-dereference", "--from=1015", "1023", "--", str(home_base / "demo1")],
        ["chown", "-R", "--no-dereference", "--from=:1016", ":1024", "--", str(home_base / "demo1")],
    ]


def test_import_errors_never_show_host_paths():
    message = portable_archive.public_import_error(portable_archive.PortableArchiveError(
        "'/var/lib/boron/cpanel-import-staging/boron-import-4-x/account.boron.tar' is not readable"))
    assert "/var/lib" not in message and "[server path]" in message
    internal = portable_archive.public_import_error(RuntimeError("lstat /home/demo1/x: denied"))
    assert "/home" not in internal and "reference" in internal


def test_non_tar_import_reports_not_a_boron_archive(tmp_path):
    archive = tmp_path / "account.boron.tar"
    archive.write_text("not a tar at all\n" * 100)
    target = tmp_path / "out"
    target.mkdir()
    with pytest.raises(portable_archive.PortableArchiveError) as caught:
        portable_archive.validate_archive(archive, target, "demo1")
    assert str(caught.value) == "The uploaded file is not a Boron archive"


def _snapshot_destination(session, name="snap"):
    row = SnapshotDestination(name=name, kind="local", path="/var/backups/x", namespace=name + "ns",
                              status="ready")
    session.add(row)
    session.flush()
    return row


def test_backup_job_without_history_is_deleted_and_frees_its_destination(isolated_db, monkeypatch):
    with write_session() as session:
        destination = _snapshot_destination(session)
        policy = SnapshotPolicy(name="nightly", destination_id=destination.id,
                                options={"destination_ids": [destination.id]})
        session.add(policy)
        session.flush()
        policy_id, destination_id = policy.id, destination.id
    assert snapshot_jobs.delete_policy({"id": policy_id})["history_kept"] is False
    assert snapshot_jobs.policies({})["policies"] == []
    assert snapshot_jobs.delete_destination({"id": destination_id})["status"] == "configuration_removed"


def test_backup_job_with_history_is_retired_keeping_runs(isolated_db):
    with write_session() as session:
        destination = _snapshot_destination(session)
        account = Account(username="demo1", status="active")
        session.add(account)
        session.flush()
        policy = SnapshotPolicy(name="nightly", destination_id=destination.id, frequency="daily",
                                options={"destination_ids": [destination.id]})
        session.add(policy)
        session.flush()
        session.add(SnapshotRun(policy_id=policy.id, destination_id=destination.id, account_id=account.id,
                                status="completed"))
        policy_id, destination_id = policy.id, destination.id
    result = snapshot_jobs.delete_policy({"id": policy_id})
    assert result["history_kept"] is True
    assert snapshot_jobs.policies({})["policies"] == []
    with write_session() as session:
        row = session.get(SnapshotPolicy, policy_id)
        assert row.enabled is False and row.name != "nightly"
        assert session.scalar(select(SnapshotRun.id)) is not None
    with pytest.raises(ValidationError, match="not found"):
        snapshot_jobs.delete_policy({"id": policy_id})
    with pytest.raises(ValidationError, match="not found"):
        snapshot_jobs.queue_policy({"id": policy_id})
    assert snapshot_jobs.delete_destination({"id": destination_id})["status"] == "configuration_removed"


def test_backup_job_with_active_run_cannot_be_deleted(isolated_db):
    with write_session() as session:
        destination = _snapshot_destination(session)
        account = Account(username="demo1", status="active")
        session.add(account)
        session.flush()
        policy = SnapshotPolicy(name="nightly", destination_id=destination.id, options={})
        session.add(policy)
        session.flush()
        session.add(SnapshotRun(policy_id=policy.id, destination_id=destination.id, account_id=account.id,
                                status="running"))
        policy_id = policy.id
    with pytest.raises(ValidationError, match="active"):
        snapshot_jobs.delete_policy({"id": policy_id})


def test_legacy_schedule_can_be_deleted(isolated_db, tmp_path):
    with write_session() as session:
        destination = BackupDestination(name="d", kind="local", local_path=str(tmp_path))
        session.add(destination)
        session.flush()
        schedule = BackupSchedule(account_id=None, frequency="daily", retention_count=7,
                                  destination_id=destination.id, enabled=True)
        session.add(schedule)
        session.flush()
        schedule_id = schedule.id
    assert backup.delete_schedule({"id": schedule_id})["status"] == "deleted"
    with write_session() as session:
        assert session.get(BackupSchedule, schedule_id) is None
    with pytest.raises(ValidationError):
        backup.delete_schedule({"id": schedule_id})


def test_new_operations_have_reviewed_policies_and_routes():
    from daemon import server

    for op in ("snapshot.policy.delete", "backup.schedule.delete", "fb.chmod"):
        assert op in server.OP_TABLE
        assert POLICY_BY_OPERATION[op] in ("global_admin", "account_username")
    paths = {(route.path, method) for route in backups_router.api_router.routes for method in route.methods}
    assert ("/api/v1/backups/snapshots/policies/{policy_id}", "DELETE") in paths
    assert ("/api/v1/backups/schedules/{schedule_id}", "DELETE") in paths


def test_local_destination_without_path_is_a_validation_error():
    from daemon import snapshot_storage

    with pytest.raises(ValidationError, match="absolute path"):
        snapshot_storage._absolute(None)


# --- File Manager ------------------------------------------------------------------------


@pytest.mark.parametrize("status,body,expected", [
    (500, b'{"status":500,"message":"could not resolve symlinks for /home/qa/etc/passwd: lstat /home/qa/etc: no such file or directory"}', 404),
    (500, b"invalid entry path ../../x: path traversal", 400),
    (404, b"lstat /root/qa: no such file or directory", 404),
    (500, b"open /home/qa/x: permission denied", 403),
    (500, b"something odd at /srv/fb/state", 502),
    (409, b"conflict at /home/qa/a", 409),
    # A 4xx keeps its own class: the SPA re-logs in on 401 and the text
    # "not allowed" / "forbidden" must not turn it into a path error.
    (405, b"method not allowed", 405),
    (401, b"forbidden: session expired", 401),
    (403, b"forbidden /home/qa/x", 403),
])
def test_file_manager_errors_are_clean_and_path_free(status, body, expected):
    response = fbr.fb_error_response(status, body)
    assert response.status_code == expected
    payload = json.loads(response.body)
    assert "/" not in payload["message"]
    assert payload["status"] == expected


def test_file_manager_proxy_rewrites_upstream_error_bodies(monkeypatch):
    import httpx
    from starlette.requests import Request

    admin = Identity(panel_user_id=1, username="adminuser", role="admin", account_id=None, auth_method="session")
    monkeypatch.setattr(fbr, "_resolve_identity", lambda _request: admin)
    monkeypatch.setattr(fbr, "require_account_access", lambda identity, target: None)

    class _FakeResp:
        status_code = 500
        headers = httpx.Headers({"content-type": "application/json"})
        closed = False

        async def aiter_bytes(self, chunk_size=65536):
            yield b'{"message":"could not resolve symlinks for /home/demo1/etc/passwd: no such file"}'

        async def aclose(self):
            _FakeResp.closed = True

    client = httpx.AsyncClient()
    monkeypatch.setattr(fbr, "_account_client", lambda _username: client)

    async def fake_send(req, **kwargs):
        return _FakeResp()

    monkeypatch.setattr(client, "send", fake_send)
    monkeypatch.setattr(fbr, "_active_streams", {})
    request = Request({"type": "http", "scheme": "https", "server": ("panel.example", 2222),
                       "path": fbr.settings.filebrowser_base_url + "/api/resources/download",
                       "query_string": b"file=/../../etc/passwd", "method": "GET",
                       "headers": [(b"host", b"panel.example:2222"),
                                   (b"cookie", (fbr.FB_TARGET_COOKIE + "=" + fbr._sign_target("demo1")).encode())]})
    response = asyncio.run(fbr.proxy(request, "api/resources/download"))
    assert response.status_code == 404
    assert b"/home" not in response.response.body
    assert _FakeResp.closed


@pytest.fixture()
def chmod_home(isolated_db, tmp_path, monkeypatch):
    from daemon import account_exec, filebrowser

    home_base = tmp_path / "home"
    home = home_base / "demo1"
    (home / "public_html").mkdir(parents=True)
    (home / "public_html" / "index.php").write_text("<?php")
    (tmp_path / "outside").write_text("x")
    os.symlink(tmp_path / "outside", home / "public_html" / "escape")
    monkeypatch.setattr(filebrowser.settings, "home_base", str(home_base))
    monkeypatch.setattr(account_exec, "wrap", lambda username, argv, **kwargs: ["as-account", username, *argv])
    commands = []
    monkeypatch.setattr("daemon.procutil.run", lambda args, **kwargs: commands.append(args) or _ok(args))
    return filebrowser, home, commands


def test_chmod_runs_as_account_for_home_relative_paths(chmod_home):
    filebrowser, home, commands = chmod_home
    result = filebrowser.change_permissions({"username": "demo1", "paths": ["/public_html/index.php"], "mode": "0644"})
    assert result["changed"] == [{"path": "/public_html/index.php", "mode": "644"}]
    assert commands == [["as-account", "demo1", "chmod", "644", "--", str(home / "public_html" / "index.php")]]


@pytest.mark.parametrize("paths,mode,match", [
    (["/public_html/index.php"], "4755", "three octal"),
    (["/public_html/index.php"], "rwx", "three octal"),
    (["/public_html/escape"], "644", "outside"),
    (["/../../etc/passwd"], "644", "outside|does not exist"),
    (["/"], "755", "managed by the panel"),
    (["/public_html/nope"], "644", "does not exist"),
])
def test_chmod_rejects_unsafe_requests(chmod_home, paths, mode, match):
    filebrowser, _home, commands = chmod_home
    with pytest.raises(ValidationError, match=match):
        filebrowser.change_permissions({"username": "demo1", "paths": paths, "mode": mode})
    assert commands == []
