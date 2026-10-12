from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3

import pytest

from daemon import portable_crypto, updates
from daemon import handlers_auth
from daemon.procutil import ProcResult
from shared.config import settings
from shared.migrations import CURRENT_SCHEMA_VERSION
from shared.models import Account, Domain, UpdateJob
from shared.db import read_session, write_session
from shared.validation import ValidationError
from scripts import control_plane_recovery, restore_panel_database, upgrade_runtime
from testsupport.seeded_panel import seed_panel_database


def _database(path: Path, value: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE fixture (value TEXT NOT NULL)")
        connection.execute("INSERT INTO fixture(value) VALUES (?)", (value,))
        connection.commit()
    finally:
        connection.close()


def _value(path: Path) -> str:
    connection = sqlite3.connect(path)
    try:
        return connection.execute("SELECT value FROM fixture").fetchone()[0]
    finally:
        connection.close()


def test_runtime_policy_blocks_undeclared_data_plane_reload(monkeypatch):
    called = []
    monkeypatch.setattr(upgrade_runtime.subprocess, "run", lambda *args, **kwargs: called.append(args))
    upgrade_runtime._ALLOWED_DATA_PLANE_RELOADS.clear()

    with pytest.raises(RuntimeError, match="does not authorize"):
        upgrade_runtime.command(["systemctl", "reload", "postfix"])

    assert called == []


def test_runtime_policy_allows_only_declared_data_plane_reload(monkeypatch):
    called = []
    monkeypatch.setattr(upgrade_runtime.subprocess, "run", lambda *args, **kwargs: called.append(args))
    upgrade_runtime._ALLOWED_DATA_PLANE_RELOADS.clear()
    upgrade_runtime._ALLOWED_DATA_PLANE_RELOADS.add("postfix.service")

    upgrade_runtime.command(["systemctl", "reload", "postfix"])

    assert called and called[0][0] == ["systemctl", "reload", "postfix"]


def test_legacy_finalizer_uses_policy_embedded_in_verified_release(tmp_path, monkeypatch):
    metadata = {
        "format": 1,
        "minimum_source_version": "3.1.0",
        "maximum_source_version": None,
        "data_plane_reloads": ["dovecot.service", "postfix.service"],
        "control_plane_restarts": ["boron-provisiond.service", "boron-api.service"],
        "runtime_rewrites": ["mail-configuration"],
    }
    (tmp_path / "release-metadata.json").write_text(json.dumps(metadata))
    monkeypatch.setattr(upgrade_runtime, "ROOT", tmp_path)

    policy = upgrade_runtime.load_release_policy(None)

    assert policy == {
        "data_plane_reloads": ["dovecot.service", "postfix.service"],
        "runtime_rewrites": ["mail-configuration"],
    }


def test_runtime_change_report_redacts_sensitive_content(tmp_path, monkeypatch):
    ordinary = tmp_path / "service.conf"
    sensitive = tmp_path / "tls-private.key"
    ordinary.write_text("token = old-secret\nworkers = 1\n")
    sensitive.write_text("old private material")
    monkeypatch.setattr(upgrade_runtime, "RUNTIME_BACKUP", tmp_path / "runtime-backup.json")
    monkeypatch.setattr(upgrade_runtime, "_runtime_paths", lambda: [ordinary, sensitive])
    monkeypatch.setattr(upgrade_runtime, "_app_runtime_records", lambda: [])
    upgrade_runtime.create_runtime_backup()
    ordinary.write_text("token = new-secret\nworkers = 2\n")
    sensitive.write_text("new private material")

    report = upgrade_runtime.write_runtime_report()
    serialized = json.dumps(report)

    assert "old-secret" not in serialized
    assert "new-secret" not in serialized
    assert "private material" not in serialized
    ordinary_row = next(row for row in report if row["path"] == str(ordinary))
    assert "[REDACTED]" in ordinary_row["diff"]
    assert "workers = 2" in ordinary_row["diff"]
    sensitive_row = next(row for row in report if row["path"] == str(sensitive))
    assert "diff" not in sensitive_row


def test_control_plane_export_is_encrypted_and_round_trips(tmp_path):
    source_db = tmp_path / "source.db"
    _database(source_db, "database-secret-fixture")
    source_config = tmp_path / "etc-boron"
    source_config.mkdir()
    (source_config / "boron.toml").write_text("panel_hostname = 'panel.example'\n")
    secret = source_config / "secrets.env"
    secret.write_text("SESSION_SECRET=top-secret-fixture\n")
    secret.chmod(0o600)
    archive = tmp_path / "control-plane.boroncp"

    result = control_plane_recovery.export_archive(
        database=source_db, config=source_config, output=archive,
        passphrase="correct horse battery staple", version="3.2.0",
    )

    assert result["bytes"] == archive.stat().st_size
    payload = archive.read_bytes()
    assert b"database-secret-fixture" not in payload
    assert b"top-secret-fixture" not in payload
    preview = control_plane_recovery.restore_archive(
        archive_path=archive, passphrase="correct horse battery staple",
        database=tmp_path / "restored.db", config=tmp_path / "restored-etc",
        overwrite_live=False, apply=False,
    )
    assert preview["validated"] is True
    assert preview["panel_version"] == "3.2.0"

    restored_db = tmp_path / "restored.db"
    restored_config = tmp_path / "restored-etc"
    restored = control_plane_recovery.restore_archive(
        archive_path=archive, passphrase="correct horse battery staple",
        database=restored_db, config=restored_config,
        overwrite_live=False, apply=True,
    )
    assert restored["restored"] is True
    assert _value(restored_db) == "database-secret-fixture"
    assert (restored_config / "secrets.env").read_text() == "SESSION_SECRET=top-secret-fixture\n"
    assert stat_mode(restored_config / "secrets.env") == 0o600


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


def test_control_plane_restore_refuses_live_target_and_wrong_key(tmp_path):
    source_db = tmp_path / "source.db"
    live_db = tmp_path / "live.db"
    _database(source_db, "source")
    _database(live_db, "live")
    config = tmp_path / "source-config"
    config.mkdir()
    (config / "boron.toml").write_text("schema_version = 1\n")
    archive = tmp_path / "control-plane.boroncp"
    control_plane_recovery.export_archive(
        database=source_db, config=config, output=archive,
        passphrase="correct horse battery staple", version="3.2.0",
    )

    with pytest.raises(portable_crypto.PortableEncryptionError):
        control_plane_recovery.restore_archive(
            archive_path=archive, passphrase="incorrect recovery phrase",
            database=tmp_path / "unused.db", config=tmp_path / "unused-config",
            overwrite_live=False, apply=False,
        )
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        control_plane_recovery.restore_archive(
            archive_path=archive, passphrase="correct horse battery staple",
            database=live_db, config=tmp_path / "live-config",
            overwrite_live=False, apply=True,
        )
    assert _value(live_db) == "live"

    preview = control_plane_recovery.restore_archive(
        archive_path=archive, passphrase="correct horse battery staple",
        database=live_db, config=tmp_path / "live-config",
        overwrite_live=False, apply=False,
    )
    assert preview["validated"] is True
    assert _value(live_db) == "live"


def test_control_plane_recovery_excludes_terminated_account_users(tmp_path):
    database = tmp_path / "accounts.db"
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "CREATE TABLE accounts (username TEXT NOT NULL, uid INTEGER, status TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO accounts (username, uid, status) VALUES (?, ?, ?)",
            [("active1", 1201, "active"), ("paused1", 1202, "suspended"),
             ("oldgone", 1203, "terminated")],
        )
        connection.commit()
    finally:
        connection.close()

    assert control_plane_recovery._accounts(database) == [
        ("active1", 1201), ("paused1", 1202),
    ]


def test_control_plane_restore_recovers_seeded_logins_and_accounts(
    isolated_db, tmp_path, monkeypatch
):
    """Exercise the fresh-root control-plane restore with production auth."""
    admin_password = "".join(("CI-admin-recovery", "-passphrase-42!"))
    customer_password = "".join(("CI-customer-recovery", "-passphrase-42!"))
    source_db = tmp_path / "source" / "boron.db"
    seeded = seed_panel_database(
        source_db,
        admin_password=admin_password,
        customer_password=customer_password,
    )
    source_config = tmp_path / "source" / "etc-boron"
    source_config.mkdir()
    (source_config / "boron.toml").write_text(
        "schema_version = 2\npanel_hostname = 'panel.example.test'\n",
        encoding="utf-8",
    )
    archive = tmp_path / "control-plane.boroncp"
    control_plane_recovery.export_archive(
        database=source_db,
        config=source_config,
        output=archive,
        passphrase="correct horse battery staple",
        version="3.2.0",
    )

    # A unit-test fresh root has no account users. Validate the identities and
    # record the reconciliation plan without changing the test host.
    monkeypatch.setattr(control_plane_recovery, "_check_uid_conflicts", lambda _rows: None)
    monkeypatch.setattr(
        control_plane_recovery,
        "_ensure_accounts",
        lambda rows, *, apply: [username for username, _uid in rows],
    )
    restored_db = tmp_path / "fresh-host" / "var-lib-boron" / "boron.db"
    restored_config = tmp_path / "fresh-host" / "etc-boron"
    restored = control_plane_recovery.restore_archive(
        archive_path=archive,
        passphrase="correct horse battery staple",
        database=restored_db,
        config=restored_config,
        overwrite_live=False,
        apply=True,
    )

    assert restored["restored"] is True
    assert restored["accounts"] == 1
    assert (restored_config / "boron.toml").is_file()

    import shared.db as db_module

    monkeypatch.setattr(settings, "db_path", str(restored_db))
    db_module._write_engine = None
    db_module._WriteSession = None
    db_module._read_engine = None
    db_module._ReadSession = None
    monkeypatch.setattr(handlers_auth.events, "emit", lambda *_args, **_kwargs: None)

    admin_login = handlers_auth.login_begin({
        "username": seeded.admin_username,
        "password": admin_password,
    })
    customer_login = handlers_auth.login_begin({
        "username": seeded.account_username,
        "password": customer_password,
    })
    assert admin_login["valid"] is True and admin_login["role"] == "admin"
    assert customer_login["valid"] is True and customer_login["role"] == "customer"
    with read_session() as db:
        account = db.query(Account).filter_by(username=seeded.account_username).one()
        domain = db.query(Domain).filter_by(domain=seeded.primary_domain).one()
        assert domain.account_id == account.id


def test_detached_database_restore_rolls_back_failed_health_check(tmp_path, monkeypatch):
    source = tmp_path / "snapshot.db"
    target = tmp_path / "live.db"
    rescue = tmp_path / "rescue.db"
    status = tmp_path / "status.json"
    _database(source, "snapshot")
    _database(target, "live")
    calls = []
    monkeypatch.setattr(
        restore_panel_database, "_services",
        lambda systemctl, action, units: (calls.append((action, tuple(units))) or True),
    )
    monkeypatch.setattr(restore_panel_database, "_healthy", lambda *_args: False)
    args = argparse.Namespace(
        source=str(source), target=str(target), rescue=str(rescue), status_file=str(status),
        health_url="https://127.0.0.1/healthz", health_timeout=0.1,
        units="boron-api.service,boron-provisiond.service", systemctl="systemctl",
    )

    result = restore_panel_database.run(args)

    assert result == 2
    assert _value(target) == "live"
    document = json.loads(status.read_text())
    assert document["state"] == "failed"
    assert document["rolled_back"] is True
    assert calls[0][0] == "stop"


def test_portable_encryption_creates_private_files(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"fixture")
    encrypted = tmp_path / "archive"
    clear = tmp_path / "clear"
    old_umask = os.umask(0)
    try:
        portable_crypto.encrypt(source, encrypted, "correct horse battery staple")
        portable_crypto.decrypt(encrypted, clear, "correct horse battery staple")
    finally:
        os.umask(old_umask)
    assert stat_mode(encrypted) == 0o600
    assert stat_mode(clear) == 0o600
    assert clear.read_bytes() == b"fixture"


def test_manual_database_restore_is_only_offered_for_compatible_snapshots(
    isolated_db, tmp_path, monkeypatch
):
    backup_root = tmp_path / "backups"
    snapshot = backup_root / "pre-update-3.2.0-20261005-120000"
    snapshot.mkdir(parents=True)
    source = sqlite3.connect(settings.db_path)
    try:
        target = sqlite3.connect(snapshot / "boron.db")
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    live = tmp_path / "boron"
    live.mkdir()
    (live / "schema-compat.json").write_text(json.dumps({
        "schema_version": 3, "minimum_supported_schema": 0, "maximum_supported_schema": 3,
    }))
    monkeypatch.setattr(settings, "update_backup_dir", str(backup_root))
    monkeypatch.setattr(settings, "update_live_dir", str(live))
    monkeypatch.setattr(updates, "_SYSTEMD_RUN", ["systemd-run", "--collect"])
    launched = []
    monkeypatch.setattr(
        updates, "run",
        lambda argv, **kwargs: (launched.append((argv, kwargs)) or ProcResult(
            args=argv, returncode=0, stdout="", stderr=""
        )),
    )

    inventory = updates.list_database_backups({})

    assert inventory["backups"][0]["schema_compatible"] is True
    result = updates.restore_database_backup({
        "backup_id": snapshot.name, "confirm": True,
    })
    assert result["started"] is True
    assert launched
    assert Path(launched[0][0][4]).name == "restore-panel-database.py"


def test_manual_database_restore_refuses_incompatible_snapshot(
    isolated_db, tmp_path, monkeypatch
):
    backup_root = tmp_path / "backups"
    snapshot = backup_root / "pre-update-future-20261005-120000"
    snapshot.mkdir(parents=True)
    source = sqlite3.connect(settings.db_path)
    try:
        target = sqlite3.connect(snapshot / "boron.db")
        try:
            source.backup(target)
            future_revision = CURRENT_SCHEMA_VERSION + 1
            target.execute(
                "INSERT INTO schema_migrations"
                "(revision, name, compatibility, checksum, app_version, applied_at) "
                "VALUES (?, 'future_breaking', 'breaking', 'x', '4.0.0', '2099-01-01')",
                (future_revision,),
            )
            target.execute(
                "UPDATE control_plane_metadata SET value=? WHERE key='schema_version'",
                (str(future_revision),),
            )
            target.commit()
        finally:
            target.close()
    finally:
        source.close()
    live = tmp_path / "boron"
    live.mkdir()
    (live / "schema-compat.json").write_text(json.dumps({
        "schema_version": 3, "minimum_supported_schema": 0, "maximum_supported_schema": 3,
    }))
    monkeypatch.setattr(settings, "update_backup_dir", str(backup_root))
    monkeypatch.setattr(settings, "update_live_dir", str(live))

    inventory = updates.list_database_backups({})

    assert inventory["backups"][0]["schema_compatible"] is False
    with pytest.raises(Exception, match="cannot be restored"):
        updates.restore_database_backup({"backup_id": snapshot.name, "confirm": True})


def test_update_requires_acknowledgement_for_declared_data_plane_services(
    isolated_db, monkeypatch
):
    metadata = {
        "format": 1, "minimum_source_version": "0.0.0", "maximum_source_version": None,
        "data_plane_reloads": ["postfix.service"],
        "control_plane_restarts": ["boron-api.service"], "runtime_rewrites": [],
        "requires_data_plane_acknowledgement": True, "signed": True, "legacy": False,
        "digest": "a" * 64,
    }
    monkeypatch.setattr(updates, "BORON_VERSION", "3.1.0")
    monkeypatch.setattr(updates, "check", lambda _params: {
        "configured": True, "latest_version": "3.2.0", "error": None,
        "release_metadata": metadata,
    })
    submitted = []
    monkeypatch.setattr(updates._executor, "submit", lambda *args: submitted.append(args))

    with pytest.raises(ValidationError, match="acknowledge"):
        updates.start_update({"initiated_by": "admin", "to_version": "3.2.0"})

    row = updates.start_update({
        "initiated_by": "admin", "to_version": "3.2.0",
        "acknowledge_data_plane": True,
    })
    assert row["acknowledged_data_plane"] == ["postfix.service"]
    assert submitted


def test_archive_metadata_must_match_signed_preview_and_acknowledgement(
    isolated_db, tmp_path
):
    release = tmp_path / "release"
    release.mkdir()
    metadata = {
        "format": 1, "minimum_source_version": "0.0.0", "maximum_source_version": None,
        "data_plane_reloads": ["postfix.service"],
        "control_plane_restarts": ["boron-api.service"], "runtime_rewrites": [],
    }
    path = release / "release-metadata.json"
    path.write_text(json.dumps(metadata))
    with write_session() as session:
        job = UpdateJob(
            kind="update", status="running", from_version=updates.BORON_VERSION,
            to_version="99.0.0", initiated_by="admin",
            release_metadata={**metadata, "digest": "0" * 64},
            acknowledged_data_plane=["postfix.service"],
        )
        session.add(job)
        session.flush()
        job_id = job.id

    with pytest.raises(updates._StepFailed):
        updates._validate_staged_release_metadata(job_id, str(release))

    with write_session() as session:
        row = session.get(UpdateJob, job_id)
        assert row.status == "failed"
        assert "preview differs" in row.error


def test_preflight_active_tasks_require_explicit_override(isolated_db, monkeypatch):
    monkeypatch.setattr(updates, "_active_task_inventory", lambda: [
        {"kind": "backup_jobs", "id": 42, "status": "running"},
    ])
    monkeypatch.setattr(updates.diagnostics, "run_all", lambda _params: {"checks": []})
    monkeypatch.setattr(settings, "update_preflight_min_free_mb", 0)
    metadata = updates._legacy_release_metadata()

    blocked = updates.preflight({"release_metadata": metadata})
    allowed = updates.preflight({
        "release_metadata": metadata, "override_active_tasks": True,
    })

    assert blocked["can_start"] is False
    assert blocked["override_required"] is True
    assert blocked["critical_failures"][0]["id"] == "active_tasks"
    assert allowed["can_start"] is True
    active_check = next(row for row in allowed["checks"] if row["id"] == "active_tasks")
    assert active_check["status"] == "warn"
