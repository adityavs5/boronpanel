from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from daemon import backup
from daemon import portable_archive as portable
from shared.db import write_session
from shared.models import Account, AccountArchiveImportJob, BackupDestination, BackupJob, PanelUser


def _portable_tar(tmp_path: Path, *, username: str = "demo1", tamper: bool = False, extra: bool = False) -> Path:
    root = tmp_path / "payload"
    root.mkdir()
    component = root / "home.tar.gz"
    with tarfile.open(component, "w:gz") as inner:
        data = b"hello"
        info = tarfile.TarInfo(f"{username}/public_html/index.html")
        info.size = len(data)
        inner.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(component.read_bytes()).hexdigest()
    manifest = {
        "format": backup.PORTABLE_ARCHIVE_FORMAT,
        "format_version": backup.PORTABLE_ARCHIVE_VERSION,
        "username": username,
        "domains": [],
        "databases": [],
        "mail_domains": [],
        "mail_users": [],
        "cron_jobs": [],
        "dns_zone": None,
        "components": [{"path": "home.tar.gz", "size_bytes": component.stat().st_size, "sha256": digest}],
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    if tamper:
        component.write_bytes(component.read_bytes() + b"changed")
    if extra:
        (root / "unlisted.txt").write_text("unexpected")
    artifact = tmp_path / "account.boron.tar"
    with tarfile.open(artifact, "w") as outer:
        outer.add(root, arcname="demo1-backup")
    return artifact


def test_validate_archive_accepts_versioned_checksummed_artifact(tmp_path):
    artifact = _portable_tar(tmp_path)
    extract = tmp_path / "extract"
    extract.mkdir()
    manifest, root = portable.validate_archive(artifact, extract, "demo1")
    assert manifest["format_version"] == 1
    assert root.name == "demo1-backup"
    assert (root / "home.tar.gz").is_file()


def test_validate_archive_rejects_tampered_component(tmp_path):
    artifact = _portable_tar(tmp_path, tamper=True)
    extract = tmp_path / "extract"
    extract.mkdir()
    with pytest.raises(portable.PortableArchiveError, match="wrong size|SHA-256"):
        portable.validate_archive(artifact, extract, "demo1")


def test_validate_archive_rejects_unlisted_component(tmp_path):
    artifact = _portable_tar(tmp_path, extra=True)
    extract = tmp_path / "extract"
    extract.mkdir()
    with pytest.raises(portable.PortableArchiveError, match="inventory mismatch"):
        portable.validate_archive(artifact, extract, "demo1")


def test_validate_archive_rejects_username_mismatch(tmp_path):
    artifact = _portable_tar(tmp_path)
    extract = tmp_path / "extract"
    extract.mkdir()
    with pytest.raises(portable.PortableArchiveError, match="target username"):
        portable.validate_archive(artifact, extract, "other1")


def test_validate_archive_rejects_unsafe_nested_tar(tmp_path):
    root = tmp_path / "payload"
    root.mkdir()
    component = root / "home.tar.gz"
    with tarfile.open(component, "w:gz") as inner:
        data = b"bad"
        info = tarfile.TarInfo("../../etc/boron-portable-test")
        info.size = len(data)
        inner.addfile(info, io.BytesIO(data))
    manifest = {
        "format": backup.PORTABLE_ARCHIVE_FORMAT,
        "format_version": 1,
        "username": "demo1",
        "components": [{
            "path": "home.tar.gz",
            "size_bytes": component.stat().st_size,
            "sha256": hashlib.sha256(component.read_bytes()).hexdigest(),
        }],
    }
    (root / "manifest.json").write_text(json.dumps(manifest))
    artifact = tmp_path / "unsafe.boron.tar"
    with tarfile.open(artifact, "w") as outer:
        outer.add(root, arcname="backup")
    extract = tmp_path / "extract"
    extract.mkdir()
    with pytest.raises(portable.PortableArchiveError, match="safe tar"):
        portable.validate_archive(artifact, extract, "demo1")


def test_nested_archive_rejects_member_count_before_restore(tmp_path, monkeypatch):
    component = tmp_path / "home.tar.gz"
    with tarfile.open(component, "w:gz") as archive:
        for name in ("demo1/a", "demo1/b"):
            info = tarfile.TarInfo(name)
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
    monkeypatch.setattr(backup, "MAX_BACKUP_ARCHIVE_MEMBERS", 1)
    with pytest.raises(portable.PortableArchiveError, match="too many members"):
        portable._validate_inner_tar(component)


def test_backup_extractor_rejects_duplicate_file_members(tmp_path):
    component = tmp_path / "home.tar"
    with tarfile.open(component, "w") as archive:
        for content in (b"a", b"b"):
            info = tarfile.TarInfo("demo1/index.php")
            info.size = 1
            archive.addfile(info, io.BytesIO(content))
    destination = tmp_path / "extracted"
    destination.mkdir()
    with pytest.raises(backup.BackupError, match="duplicate file path"):
        backup._safe_extract_tar(component, destination)
    assert list(destination.iterdir()) == []


def test_import_password_is_revealed_once(isolated_db):
    with write_session() as session:
        row = AccountArchiveImportJob(
            username="demo1", status="completed", progress_message="restored", initial_password="one-time-secret"
        )
        session.add(row)
        session.flush()
        job_id = row.id
    first = portable.get_job({"job_id": job_id})
    second = portable.get_job({"job_id": job_id})
    assert first["initial_password"] == "one-time-secret"
    assert "initial_password" not in second


def test_prepare_download_copies_local_full_backup(isolated_db, tmp_path, monkeypatch):
    artifact = tmp_path / "source.boron.tar"
    artifact.write_bytes(b"portable")
    with write_session() as session:
        account = Account(username="demo1", status="active", uid=2000, gid=2000)
        destination = BackupDestination(name="local", kind="local", local_path=str(tmp_path))
        session.add_all([account, destination])
        session.flush()
        job = BackupJob(
            account_id=account.id,
            kind="full",
            destination_id=destination.id,
            status="completed",
            artifact_path=str(artifact),
        )
        session.add(job)
        session.flush()
        job_id = job.id
    monkeypatch.setattr(backup, "_fetch_artifact_locally", lambda path, kind, temp: path)
    monkeypatch.setattr(portable.pwd, "getpwnam", lambda _name: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()))
    monkeypatch.setattr(portable.settings, "snapshot_download_dir", str(tmp_path / "downloads"))
    prepared = portable.prepare_download({"job_id": job_id})
    try:
        exported = Path(prepared["path"])
        assert exported.name == "demo1.boron.tar"
        assert exported.read_bytes() == b"portable"
        assert exported.stat().st_mode & 0o777 == 0o640
        export_dir = Path(prepared["cleanup_dir"])
        export_root = Path(portable.settings.snapshot_download_dir)
        assert export_root.stat().st_mode & 0o777 == 0o710
        assert export_dir.stat().st_uid == os.geteuid()
        assert export_dir.stat().st_mode & 0o777 == 0o710
        assert prepared["cleanup_id"] == export_dir.name
        assert portable.cleanup_download({"cleanup_id": prepared["cleanup_id"]}) == {"removed": True}
        assert not export_dir.exists()
    finally:
        import shutil
        shutil.rmtree(prepared["cleanup_dir"], ignore_errors=True)


def test_portable_export_cleanup_rejects_paths(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(portable.settings, "snapshot_download_dir", str(tmp_path / "downloads"))
    monkeypatch.setattr(portable.pwd, "getpwnam", lambda _name: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()))
    with pytest.raises(Exception, match="cleanup identifier"):
        portable.cleanup_download({"cleanup_id": "../outside"})


def test_trigger_import_rejects_non_staging_path(isolated_db, tmp_path):
    bad = tmp_path / "backup.boron.tar"
    bad.write_bytes(b"x")
    with pytest.raises(Exception, match="staging path"):
        portable.trigger_import({"username": "demo1", "source_ref": str(bad)})


def test_portable_upload_reservation_is_private_and_cleanup_is_scoped(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(portable.settings, "cpanel_import_staging_dir", str(tmp_path / "imports"))
    monkeypatch.setattr(portable.pwd, "getpwnam", lambda _name: SimpleNamespace(pw_uid=os.getuid(), pw_gid=os.getgid()))
    reserved = portable.reserve_upload({})
    path = Path(reserved["path"])
    assert path.parent == tmp_path / "imports" / "uploads"
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.stat().st_uid == os.geteuid()
    assert portable.cleanup_upload({"upload_id": reserved["upload_id"]}) == {"removed": True}
    assert not path.exists()
    with pytest.raises(Exception, match="upload identifier"):
        portable.cleanup_upload({"upload_id": "../outside"})


def test_fresh_portable_import_creates_customer_panel_login(isolated_db, tmp_path, monkeypatch):
    built = _portable_tar(tmp_path)
    source = tmp_path / "boron-archive-import-panel-login.boron.tar"
    shutil.copy2(built, source)
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setattr(portable.settings, "cpanel_import_staging_dir", str(staging))

    with write_session() as session:
        job = AccountArchiveImportJob(username="demo1", source_ref=str(source), status="pending")
        session.add(job)
        session.flush()
        job_id = job.id

    def fake_restore(username, _status, _archive, _tmp_dir, **_kwargs):
        with write_session() as session:
            account = session.query(Account).filter_by(username=username).one()
            account.status = "active"
            account.uid = 2000
            account.gid = 2000
        return {"initial_password": "Imported-Account-42!", "mailboxes_requiring_password_reset": []}

    monkeypatch.setattr(backup, "_restore_full", fake_restore)
    portable._run_import(job_id, "demo1", str(source))

    with write_session() as session:
        job = session.get(AccountArchiveImportJob, job_id)
        account = session.query(Account).filter_by(username="demo1").one()
        login = session.query(PanelUser).filter_by(username="demo1").one()
        assert job.status == "completed"
        assert login.role == "customer"
        assert login.account_id == account.id


def test_portable_import_worker_rechecks_target_account_before_restore(isolated_db, tmp_path, monkeypatch):
    source = tmp_path / "boron-archive-import-stale-target.boron.tar"
    source.write_bytes(b"unused")
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setattr(portable.settings, "cpanel_import_staging_dir", str(staging))

    with write_session() as session:
        job = AccountArchiveImportJob(username="demo1", source_ref=str(source), status="pending")
        session.add(job)
        session.add(Account(username="demo1", uid=2000, gid=2000, status="active"))
        session.flush()
        job_id = job.id

    monkeypatch.setattr(portable, "validate_archive", lambda *_a, **_k: pytest.fail("stale portable import must not validate archive"))
    monkeypatch.setattr(backup, "_restore_full", lambda *_a, **_k: pytest.fail("stale portable import must not restore"))

    portable._run_import(job_id, "demo1", str(source))

    with write_session() as session:
        job = session.get(AccountArchiveImportJob, job_id)
        assert job.status == "failed"
        assert "already exists" in job.error
    assert not source.exists()
