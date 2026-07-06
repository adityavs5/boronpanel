import gzip
import json
import tarfile

import pytest
from sqlalchemy import select

from daemon import backup
from shared.db import write_session
from shared.models import (
    Account,
    BackupDestination,
    BackupJob,
    BackupSchedule,
    DatabaseGrant,
    Domain,
    MailDomain,
    MailUser,
    RestoreJob,
)


def make_account(session, **overrides):
    defaults = dict(username="demo1", status="active", uid=2000, gid=2000)
    defaults.update(overrides)
    account = Account(**defaults)
    session.add(account)
    session.flush()
    return account


@pytest.fixture()
def fake_home(tmp_path, monkeypatch):
    home_base = tmp_path / "home"
    home_base.mkdir()
    monkeypatch.setattr(backup.settings, "home_base", str(home_base))
    return home_base


@pytest.fixture()
def fake_mail_base(tmp_path, monkeypatch):
    mail_base = tmp_path / "vmail"
    mail_base.mkdir()
    monkeypatch.setattr(backup.settings, "mail_base", str(mail_base))
    return mail_base


@pytest.fixture()
def fake_staging(tmp_path, monkeypatch):
    staging = tmp_path / "staging"
    staging.mkdir()
    monkeypatch.setattr(backup.settings, "backup_staging_dir", str(staging))
    return staging


@pytest.fixture()
def stub_dump_database(monkeypatch):
    calls = []

    def fake_dump(db_name, output_path):
        calls.append(db_name)
        with gzip.open(output_path, "wt") as f:
            f.write(f"-- fake dump of {db_name}\n")

    monkeypatch.setattr(backup, "_dump_database", fake_dump)
    return calls


@pytest.fixture()
def stub_cron_and_dns(monkeypatch):
    monkeypatch.setattr(backup.cron, "list_jobs", lambda username: [])
    monkeypatch.setattr(backup.powerdns, "list_records", lambda zone: [])


@pytest.fixture()
def stub_executor(monkeypatch):
    submitted = []

    def fake_submit(fn, *args):
        submitted.append((fn, args))

    monkeypatch.setattr(backup._executor, "submit", fake_submit)
    return submitted


@pytest.fixture()
def stub_rclone(monkeypatch):
    calls = {"create": [], "delete": [], "copy": [], "copy_from_remote": []}
    monkeypatch.setattr(backup.rclone, "create_remote", lambda name, kind, cfg: calls["create"].append((name, kind, cfg)))
    monkeypatch.setattr(backup.rclone, "delete_remote", lambda name: calls["delete"].append(name))
    monkeypatch.setattr(backup.rclone, "copy", lambda local, remote: calls["copy"].append((local, remote)))
    monkeypatch.setattr(backup.rclone, "copy_from_remote", lambda remote, local: calls["copy_from_remote"].append((remote, local)))
    return calls


def make_destination(session, **overrides):
    defaults = dict(name="local1", kind="local", local_path="/tmp/nonexistent-backups")
    defaults.update(overrides)
    dest = BackupDestination(**defaults)
    session.add(dest)
    session.flush()
    return dest


# --- destinations -------------------------------------------------------------


def test_create_local_destination(isolated_db, tmp_path):
    local_path = str(tmp_path / "backups")
    result = backup.create_destination({"name": "mylocal", "kind": "local", "local_path": local_path})
    assert result["kind"] == "local"
    assert result["local_path"] == local_path
    import os
    assert os.path.isdir(local_path)


def test_create_rclone_destination(isolated_db, stub_rclone):
    result = backup.create_destination(
        {"name": "mys3", "kind": "rclone", "rclone_remote_type": "s3", "rclone_config": {"provider": "AWS"}, "rclone_path_prefix": "/backups/"}
    )
    assert result["kind"] == "rclone"
    assert result["rclone_remote"] == "forgehost_mys3"
    assert result["rclone_path_prefix"] == "backups"
    assert stub_rclone["create"] == [("forgehost_mys3", "s3", {"provider": "AWS"})]


def test_create_destination_rejects_duplicate_name(isolated_db, tmp_path):
    backup.create_destination({"name": "dup", "kind": "local", "local_path": str(tmp_path / "a")})
    with pytest.raises(backup.BackupError):
        backup.create_destination({"name": "dup", "kind": "local", "local_path": str(tmp_path / "b")})


def test_delete_destination_removes_rclone_remote(isolated_db, stub_rclone):
    dest = backup.create_destination({"name": "mys3", "kind": "rclone", "rclone_remote_type": "s3", "rclone_config": {}})
    backup.delete_destination({"id": dest["id"]})
    assert stub_rclone["delete"] == ["forgehost_mys3"]


def test_delete_destination_refuses_if_schedule_references_it(isolated_db, tmp_path):
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    backup.set_schedule({"frequency": "daily", "retention_count": 5, "destination_id": dest["id"]})
    with pytest.raises(backup.BackupError):
        backup.delete_destination({"id": dest["id"]})


def test_delete_destination_refuses_if_backup_jobs_reference_it(isolated_db, tmp_path):
    """A real bug found live: deleting a destination with completed backup
    jobs recorded against it hit BackupJob's own foreign key and surfaced
    a raw SQL IntegrityError instead of a clean explanation."""
    with write_session() as session:
        account = make_account(session)
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    with write_session() as session:
        session.add(BackupJob(account_id=account.id, kind="full", destination_id=dest["id"], status="completed", artifact_path="/tmp/x.tar"))
    with pytest.raises(backup.BackupError):
        backup.delete_destination({"id": dest["id"]})


# --- schedules -----------------------------------------------------------------


def test_set_schedule_server_default(isolated_db, tmp_path):
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    result = backup.set_schedule({"frequency": "weekly", "retention_count": 4, "destination_id": dest["id"]})
    assert result["account_id"] is None
    assert result["frequency"] == "weekly"


def test_set_schedule_per_account(isolated_db, tmp_path):
    with write_session() as session:
        account = make_account(session)
        account_id = account.id
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    result = backup.set_schedule({"username": "demo1", "frequency": "monthly", "retention_count": 2, "destination_id": dest["id"]})
    assert result["account_id"] == account_id


def test_set_schedule_upserts_existing(isolated_db, tmp_path):
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    backup.set_schedule({"frequency": "daily", "retention_count": 7, "destination_id": dest["id"]})
    result = backup.set_schedule({"frequency": "weekly", "retention_count": 3, "destination_id": dest["id"]})
    assert result["frequency"] == "weekly"
    with write_session() as session:
        count = len(session.scalars(select(BackupSchedule)).all())
    assert count == 1


def test_get_effective_schedule_falls_back_to_default(isolated_db, tmp_path):
    with write_session() as session:
        account = make_account(session)
        account_id = account.id
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    backup.set_schedule({"frequency": "daily", "retention_count": 7, "destination_id": dest["id"]})  # server default
    with write_session() as session:
        effective = backup.get_effective_schedule(session, account_id)
        assert effective.account_id is None


def test_get_effective_schedule_prefers_own_over_default(isolated_db, tmp_path):
    with write_session() as session:
        account = make_account(session)
        account_id = account.id
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    backup.set_schedule({"frequency": "daily", "retention_count": 7, "destination_id": dest["id"]})
    backup.set_schedule({"username": "demo1", "frequency": "monthly", "retention_count": 2, "destination_id": dest["id"]})
    with write_session() as session:
        effective = backup.get_effective_schedule(session, account_id)
        assert effective.account_id == account_id
        assert effective.frequency == "monthly"


def test_set_schedule_rejects_bad_frequency(isolated_db, tmp_path):
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    with pytest.raises(Exception):
        backup.set_schedule({"frequency": "hourly", "retention_count": 1, "destination_id": dest["id"]})


# --- building backup artifacts ---------------------------------------------------


def test_build_full_backup_creates_manifest_and_tar(isolated_db, fake_home, fake_mail_base, fake_staging, stub_dump_database, stub_cron_and_dns):
    with write_session() as session:
        account = make_account(session)
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot=f"{fake_home}/demo1/public_html"))
        session.add(DatabaseGrant(account_id=account.id, db_name="demo1_shop", db_user="demo1_shop"))
        md = MailDomain(account_id=account.id, domain="demo1.example")
        session.add(md)
        session.flush()
        session.add(MailUser(mail_domain_id=md.id, local_part="alice", domain="demo1.example"))

    home_dir = fake_home / "demo1" / "public_html"
    home_dir.mkdir(parents=True)
    (home_dir / "index.html").write_text("hello")
    mail_dir = fake_mail_base / "demo1.example" / "alice"
    mail_dir.mkdir(parents=True)
    (mail_dir / "test.eml").write_text("From: test\n")

    staging_dir = fake_staging / "job-1"
    staging_dir.mkdir()
    artifact = backup._build_full_backup("demo1", staging_dir, job_id=1)

    assert artifact.exists()
    assert stub_dump_database == ["demo1_shop"]

    with tarfile.open(artifact) as tf:
        names = tf.getnames()
        manifest_name = next(n for n in names if n.endswith("manifest.json"))
        manifest = json.loads(tf.extractfile(manifest_name).read())
    assert manifest["username"] == "demo1"
    assert manifest["domains"][0]["domain"] == "demo1.example"
    assert manifest["databases"][0]["db_name"] == "demo1_shop"
    assert manifest["mail_domains"] == ["demo1.example"]
    assert manifest["mail_users"][0]["local_part"] == "alice"
    assert any(n.endswith("home.tar.gz") for n in names)
    assert any(n.endswith("databases/demo1_shop.sql.gz") for n in names)
    assert any(n.endswith("mail/demo1.example.tar.gz") for n in names)


def test_build_file_backup_rejects_path_traversal(isolated_db, fake_home, fake_staging):
    (fake_home / "demo1").mkdir()
    staging_dir = fake_staging / "job-2"
    staging_dir.mkdir()
    with pytest.raises(backup.BackupError):
        backup._build_file_backup("demo1", "../../../etc/passwd", staging_dir, job_id=2)


def test_build_file_backup_packages_one_file(isolated_db, fake_home, fake_staging):
    account_home = fake_home / "demo1" / "public_html"
    account_home.mkdir(parents=True)
    (account_home / "config.php").write_text("<?php\n")
    staging_dir = fake_staging / "job-3"
    staging_dir.mkdir()

    artifact = backup._build_file_backup("demo1", "public_html/config.php", staging_dir, job_id=3)
    with tarfile.open(artifact) as tf:
        assert "public_html/config.php" in tf.getnames()


def test_build_mailbox_backup_requires_at_sign(isolated_db, fake_mail_base, fake_staging):
    staging_dir = fake_staging / "job-4"
    staging_dir.mkdir()
    with pytest.raises(backup.BackupError):
        backup._build_mailbox_backup("not-an-email", staging_dir, job_id=4)


def test_build_mailbox_backup_packages_maildir(isolated_db, fake_mail_base, fake_staging):
    mailbox_dir = fake_mail_base / "demo1.example" / "alice"
    mailbox_dir.mkdir(parents=True)
    (mailbox_dir / "msg.eml").write_text("hi")
    staging_dir = fake_staging / "job-5"
    staging_dir.mkdir()

    artifact = backup._build_mailbox_backup("alice@demo1.example", staging_dir, job_id=5)
    with tarfile.open(artifact) as tf:
        assert "alice/msg.eml" in tf.getnames()


def test_build_database_backup_calls_dump(isolated_db, fake_staging, stub_dump_database):
    staging_dir = fake_staging / "job-6"
    staging_dir.mkdir()
    artifact = backup._build_database_backup("demo1_shop", staging_dir, job_id=6)
    assert artifact.exists()
    assert stub_dump_database == ["demo1_shop"]


# --- running backup jobs end to end (synchronous, no thread pool) -----------------


def test_run_backup_job_full_to_local_destination(isolated_db, fake_home, fake_mail_base, fake_staging, stub_dump_database, stub_cron_and_dns, tmp_path):
    with write_session() as session:
        account = make_account(session)
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot=f"{fake_home}/demo1/public_html"))
    (fake_home / "demo1").mkdir()

    dest_path = tmp_path / "dest"
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(dest_path)})

    with write_session() as session:
        job = BackupJob(account_id=account.id, kind="full", destination_id=dest["id"], status="pending", progress_message="queued")
        session.add(job)
        session.flush()
        job_id = job.id

    backup._run_backup_job(job_id)

    with write_session() as session:
        completed = session.get(BackupJob, job_id)
        assert completed.status == "completed"
        assert completed.artifact_path is not None
        import os
        assert os.path.exists(completed.artifact_path)
        assert completed.size_bytes > 0


def test_run_backup_job_full_emits_backup_completed_event(isolated_db, fake_home, fake_mail_base, fake_staging, stub_dump_database, stub_cron_and_dns, tmp_path, monkeypatch):
    with write_session() as session:
        account = make_account(session)
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot=f"{fake_home}/demo1/public_html"))
    (fake_home / "demo1").mkdir()
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "dest")})

    emitted = []
    monkeypatch.setattr(backup.events, "emit", lambda event_type, account, **ctx: emitted.append((event_type, ctx)))

    with write_session() as session:
        job = BackupJob(account_id=account.id, kind="full", destination_id=dest["id"], status="pending", progress_message="queued")
        session.add(job)
        session.flush()
        job_id = job.id

    backup._run_backup_job(job_id)
    assert emitted == [("backup.completed", {"job_id": job_id})]


def test_run_backup_job_granular_does_not_emit_event(isolated_db, fake_home, fake_staging, stub_dump_database, tmp_path, monkeypatch):
    """The goal's own scope is full-account backups only -- a granular
    database/file/mailbox backup (a much more routine, frequent action) must
    not fire the "backup completed" channel."""
    with write_session() as session:
        account = make_account(session)
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "dest")})

    emitted = []
    monkeypatch.setattr(backup.events, "emit", lambda event_type, account, **ctx: emitted.append(event_type))

    with write_session() as session:
        job = BackupJob(account_id=account.id, kind="database", item_ref="demo1_app", destination_id=dest["id"], status="pending", progress_message="queued")
        session.add(job)
        session.flush()
        job_id = job.id

    backup._run_backup_job(job_id)
    with write_session() as session:
        assert session.get(BackupJob, job_id).status == "completed"
    assert emitted == []


def test_run_backup_job_marks_failed_on_error(isolated_db, fake_home, fake_staging, tmp_path, monkeypatch):
    with write_session() as session:
        account = make_account(session)
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "dest")})

    monkeypatch.setattr(backup, "_build_full_backup", lambda *a, **k: (_ for _ in ()).throw(backup.BackupError("boom")))

    with write_session() as session:
        job = BackupJob(account_id=account.id, kind="full", destination_id=dest["id"], status="pending", progress_message="queued")
        session.add(job)
        session.flush()
        job_id = job.id

    backup._run_backup_job(job_id)

    with write_session() as session:
        failed = session.get(BackupJob, job_id)
        assert failed.status == "failed"
        assert "boom" in failed.error


def test_run_backup_job_uploads_to_rclone_destination(isolated_db, fake_home, fake_staging, stub_rclone, monkeypatch):
    with write_session() as session:
        account = make_account(session)
    (fake_home / "demo1").mkdir()

    def fake_build_database_backup(item_ref, staging_dir, job_id):
        artifact = staging_dir / "database.sql.gz"
        artifact.write_bytes(b"x")
        return artifact

    monkeypatch.setattr(backup, "_build_database_backup", fake_build_database_backup)

    dest = backup.create_destination({"name": "mys3", "kind": "rclone", "rclone_remote_type": "s3", "rclone_config": {}})
    with write_session() as session:
        job = BackupJob(account_id=account.id, kind="database", item_ref="demo1_shop", destination_id=dest["id"], status="pending")
        session.add(job)
        session.flush()
        job_id = job.id

    backup._run_backup_job(job_id)

    with write_session() as session:
        completed = session.get(BackupJob, job_id)
        assert completed.status == "completed"
        assert completed.artifact_path.startswith("forgehost_mys3:")
    assert len(stub_rclone["copy"]) == 1


# --- trigger_backup (job creation only, executor stubbed) --------------------------


def test_trigger_backup_creates_job_and_submits(isolated_db, tmp_path, stub_executor):
    with write_session() as session:
        make_account(session)
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})

    result = backup.trigger_backup({"username": "demo1", "kind": "full", "destination_id": dest["id"]})
    assert result["status"] == "pending"
    assert len(stub_executor) == 1
    assert stub_executor[0][0] is backup._run_backup_job


def test_trigger_backup_rejects_when_already_pending(isolated_db, tmp_path, stub_executor):
    """Security audit finding F9: manual backup triggers had no per-account
    concurrency limit and backup artifacts live outside the account's own
    jailed home (so disk quota never capped this) -- a customer spamming
    backup.job.trigger could queue unboundedly and starve other accounts'
    real backups behind it."""
    with write_session() as session:
        make_account(session)
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})

    first = backup.trigger_backup({"username": "demo1", "kind": "full", "destination_id": dest["id"]})
    assert first["status"] == "pending"

    with pytest.raises(backup.BackupError):
        backup.trigger_backup({"username": "demo1", "kind": "full", "destination_id": dest["id"]})
    assert len(stub_executor) == 1  # the second attempt never submitted a job


def test_trigger_backup_allowed_again_after_prior_job_completes(isolated_db, tmp_path, stub_executor):
    with write_session() as session:
        make_account(session)
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})

    first = backup.trigger_backup({"username": "demo1", "kind": "full", "destination_id": dest["id"]})
    with write_session() as session:
        job = session.get(backup.BackupJob, first["id"])
        job.status = "completed"

    second = backup.trigger_backup({"username": "demo1", "kind": "full", "destination_id": dest["id"]})
    assert second["status"] == "pending"
    assert len(stub_executor) == 2


def test_trigger_backup_resolves_destination_from_schedule(isolated_db, tmp_path, stub_executor):
    with write_session() as session:
        make_account(session)
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    backup.set_schedule({"frequency": "daily", "retention_count": 7, "destination_id": dest["id"]})

    result = backup.trigger_backup({"username": "demo1", "kind": "full"})
    assert result["destination_id"] == dest["id"]


def test_trigger_backup_without_destination_or_schedule_raises(isolated_db, stub_executor):
    with write_session() as session:
        make_account(session)
    with pytest.raises(backup.BackupError):
        backup.trigger_backup({"username": "demo1", "kind": "full"})


def test_trigger_backup_requires_item_ref_for_granular_kinds(isolated_db, tmp_path, stub_executor):
    with write_session() as session:
        make_account(session)
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    with pytest.raises(Exception):
        backup.trigger_backup({"username": "demo1", "kind": "database", "destination_id": dest["id"]})


def test_trigger_backup_unknown_account_raises(isolated_db, stub_executor):
    with pytest.raises(backup.BackupError):
        backup.trigger_backup({"username": "ghost", "kind": "full"})


def test_list_jobs_filters_by_username(isolated_db, tmp_path, stub_executor):
    with write_session() as session:
        make_account(session, username="demo1")
        make_account(session, username="demo2")
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    backup.trigger_backup({"username": "demo1", "kind": "full", "destination_id": dest["id"]})
    backup.trigger_backup({"username": "demo2", "kind": "full", "destination_id": dest["id"]})

    result = backup.list_jobs({"username": "demo1"})
    assert len(result["jobs"]) == 1


# --- retention ------------------------------------------------------------------


def test_retention_deletes_oldest_beyond_count(isolated_db, tmp_path):
    with write_session() as session:
        account = make_account(session)
        account_id = account.id
    dest_path = tmp_path / "dest"
    dest_path.mkdir()
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(dest_path)})
    backup.set_schedule({"frequency": "daily", "retention_count": 2, "destination_id": dest["id"]})

    import datetime as dt

    job_ids = []
    with write_session() as session:
        for i in range(4):
            artifact = dest_path / f"artifact-{i}.tar"
            artifact.write_bytes(b"x")
            job = BackupJob(
                account_id=account_id,
                kind="full",
                destination_id=dest["id"],
                status="completed",
                artifact_path=str(artifact),
                completed_at=backup.utcnow() + dt.timedelta(seconds=i),
            )
            session.add(job)
            session.flush()
            job_ids.append(job.id)

    backup._enforce_retention(job_ids[-1])

    with write_session() as session:
        remaining = session.scalars(select(BackupJob).where(BackupJob.account_id == account_id)).all()
    assert len(remaining) == 2
    import os
    assert not os.path.exists(str(dest_path / "artifact-0.tar"))
    assert not os.path.exists(str(dest_path / "artifact-1.tar"))
    assert os.path.exists(str(dest_path / "artifact-2.tar"))
    assert os.path.exists(str(dest_path / "artifact-3.tar"))


# --- browsing ------------------------------------------------------------------


def test_browse_full_backup_reads_manifest(isolated_db, fake_staging, tmp_path):
    with write_session() as session:
        account = make_account(session)
    dest_path = tmp_path / "dest"
    dest_path.mkdir()
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(dest_path)})

    content_dir = fake_staging / "content"
    content_dir.mkdir()
    (content_dir / "manifest.json").write_text(json.dumps({"username": "demo1", "backed_up_at": "now", "domains": [], "databases": [], "mail_domains": [], "mail_users": [], "cron_jobs": [], "dns_zone": None, "php_version": "8.3"}))
    artifact = dest_path / "demo1_full.tar"
    with tarfile.open(artifact, "w") as tf:
        tf.add(content_dir, arcname="content")

    with write_session() as session:
        job = BackupJob(account_id=account.id, kind="full", destination_id=dest["id"], status="completed", artifact_path=str(artifact))
        session.add(job)
        session.flush()
        job_id = job.id

    result = backup.browse_backup({"job_id": job_id, "username": account.username})
    assert result["kind"] == "full"
    assert result["manifest"]["username"] == "demo1"
    assert any(n.endswith("manifest.json") for n in result["contents"])


def test_browse_non_full_backup_returns_item_ref(isolated_db, tmp_path):
    with write_session() as session:
        account = make_account(session)
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    with write_session() as session:
        job = BackupJob(account_id=account.id, kind="database", item_ref="demo1_shop", destination_id=dest["id"], status="completed", artifact_path="/tmp/x.sql.gz")
        session.add(job)
        session.flush()
        job_id = job.id

    result = backup.browse_backup({"job_id": job_id, "username": account.username})
    assert result == {"kind": "database", "item_ref": "demo1_shop"}


def test_browse_incomplete_backup_raises(isolated_db, tmp_path):
    with write_session() as session:
        account = make_account(session)
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    with write_session() as session:
        job = BackupJob(account_id=account.id, kind="full", destination_id=dest["id"], status="running")
        session.add(job)
        session.flush()
        job_id = job.id
    with pytest.raises(backup.BackupError):
        backup.browse_backup({"job_id": job_id, "username": account.username})


# --- restore ---------------------------------------------------------------------


def test_restore_database_dump_requires_existing_database(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(backup.mariadb, "database_exists", lambda name: False)
    dump = tmp_path / "dump.sql.gz"
    with gzip.open(dump, "wt") as f:
        f.write("SELECT 1;")
    with pytest.raises(backup.BackupError):
        backup._restore_database_dump("demo1_shop", dump)


def test_restore_database_dump_runs_mysql_restore(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(backup.mariadb, "database_exists", lambda name: True)
    ran = []

    def fake_run(args, input_text=None, timeout=30):
        ran.append((args, input_text))
        from daemon.procutil import ProcResult
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(backup, "run", fake_run)
    dump = tmp_path / "dump.sql.gz"
    with gzip.open(dump, "wt") as f:
        f.write("INSERT INTO t VALUES (1);")

    backup._restore_database_dump("demo1_shop", dump)
    assert any(args[0] == "mysql" and "demo1_shop" in args for args, _ in ran)


def test_restore_mailbox_requires_at_sign(isolated_db):
    with pytest.raises(backup.BackupError):
        backup._restore_mailbox("not-an-email", "/tmp/x.tar.gz", restore_job_id=1)


def test_restore_mailbox_requires_mail_domain_dir(isolated_db, fake_mail_base):
    with pytest.raises(backup.BackupError):
        backup._restore_mailbox("alice@nope.example", "/tmp/x.tar.gz", restore_job_id=1)


def test_trigger_restore_requires_completed_backup(isolated_db, tmp_path, stub_executor):
    with write_session() as session:
        account = make_account(session)
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    with write_session() as session:
        job = BackupJob(account_id=account.id, kind="full", destination_id=dest["id"], status="running")
        session.add(job)
        session.flush()
        job_id = job.id
    with pytest.raises(backup.BackupError):
        backup.trigger_restore({"backup_job_id": job_id, "username": account.username})


def test_trigger_restore_creates_row_and_submits(isolated_db, tmp_path, stub_executor):
    with write_session() as session:
        account = make_account(session)
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    with write_session() as session:
        job = BackupJob(account_id=account.id, kind="database", item_ref="demo1_shop", destination_id=dest["id"], status="completed", artifact_path="/tmp/x.sql.gz")
        session.add(job)
        session.flush()
        job_id = job.id

    result = backup.trigger_restore({"backup_job_id": job_id, "username": account.username})
    assert result["kind"] == "database"
    assert result["item_ref"] == "demo1_shop"
    assert len(stub_executor) == 1


def test_trigger_restore_rejects_mismatched_kind_for_non_full_artifact(isolated_db, tmp_path, stub_executor):
    with write_session() as session:
        account = make_account(session)
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    with write_session() as session:
        job = BackupJob(account_id=account.id, kind="database", item_ref="demo1_shop", destination_id=dest["id"], status="completed", artifact_path="/tmp/x.sql.gz")
        session.add(job)
        session.flush()
        job_id = job.id
    with pytest.raises(backup.BackupError):
        backup.trigger_restore({"backup_job_id": job_id, "username": account.username, "kind": "mailbox"})


def test_restore_full_reactivates_terminated_account_not_create(isolated_db, fake_home, fake_mail_base, fake_staging, tmp_path, monkeypatch):
    """A real bug found live: restoring a full backup onto a terminated
    account must REACTIVATE its existing row (reactivate_account), not
    call create_account -- terminating an account never deletes its DB
    row, so create_account's own "already exists" guard always fires,
    and _restore_full used to silently swallow that as a harmless no-op
    without ever actually recreating the Linux user."""
    with write_session() as session:
        account = make_account(session, status="terminated")
        account_id = account.id

    manifest = {
        "username": "demo1", "php_version": "8.2", "quota_soft_mb": 1, "quota_hard_mb": 2,
        "cpu_pct": 10, "mem_mb": 20, "io_mb": 5, "pids_max": 5,
        "domains": [], "databases": [], "mail_domains": [], "mail_users": [], "cron_jobs": [], "dns_zone": None,
    }
    content_dir = fake_staging / "extracted_test" / "job-1"
    content_dir.mkdir(parents=True)
    (content_dir / "manifest.json").write_text(json.dumps(manifest))
    artifact = fake_staging / "job-1.tar"
    with tarfile.open(artifact, "w") as tf:
        tf.add(content_dir, arcname="job-1")

    reactivate_calls = []
    create_calls = []
    import daemon.handlers_account as real_handlers_account

    monkeypatch.setattr(real_handlers_account, "reactivate_account", lambda params: reactivate_calls.append(params) or {"status": "active"})
    monkeypatch.setattr(real_handlers_account, "create_account", lambda params: create_calls.append(params) or (_ for _ in ()).throw(RuntimeError("must not be called")))

    tmp_dir = str(fake_staging / "restore_tmp")
    import os

    os.makedirs(tmp_dir)
    backup._restore_full("demo1", "terminated", str(artifact), tmp_dir, restore_job_id=1)

    assert len(reactivate_calls) == 1
    assert reactivate_calls[0]["php_version"] == "8.2"
    assert create_calls == []


def test_restore_full_rejects_tar_slip(isolated_db, fake_home, fake_mail_base, fake_staging):
    """Security audit finding F8: tarfile.extractall() must reject a
    member that would escape the extraction directory (filter="data",
    Python 3.12+) -- the same tar-slip class as the zip-slip fix in
    daemon/appinstaller.py/wordpress.py, applied here to Forgehost's own
    backup artifacts (restore runs as root, before anything is chowned
    back to the account)."""
    import io
    import os

    with write_session() as session:
        make_account(session, status="terminated")

    artifact = fake_staging / "evil.tar"
    with tarfile.open(artifact, "w") as tf:
        data = b"pwned"
        info = tarfile.TarInfo(name="../../../../tmp/forgehost_tarslip.txt")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))

    tmp_dir = str(fake_staging / "restore_tmp")
    os.makedirs(tmp_dir)

    with pytest.raises(Exception):
        backup._restore_full("demo1", "terminated", str(artifact), tmp_dir, restore_job_id=1)
    assert not os.path.exists("/tmp/forgehost_tarslip.txt")


def test_restore_full_reprovisions_vhost_even_when_domain_row_preexisted(isolated_db, fake_home, fake_mail_base, fake_staging, monkeypatch):
    """A real bug found live: Domain rows (like Account rows) survive
    termination, so add_domain's "already in use" guard fires for every
    domain on a restore -- but that guard fires BEFORE add_domain's own
    ols.provision_vhost() side effect runs, so the domain showed up in
    domain.list after restore while the site kept 404ing, since its vhost
    was never actually recreated. _restore_full must force one
    unconditional provision_vhost() covering every domain regardless."""
    with write_session() as session:
        account = make_account(session, status="terminated")
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot=f"{fake_home}/demo1/public_html"))

    manifest = {
        "username": "demo1", "php_version": "8.3", "quota_soft_mb": 1, "quota_hard_mb": 2,
        "cpu_pct": 10, "mem_mb": 20, "io_mb": 5, "pids_max": 5,
        "domains": [{"domain": "demo1.example", "kind": "primary"}],
        "databases": [], "mail_domains": [], "mail_users": [], "cron_jobs": [], "dns_zone": None,
    }
    content_dir = fake_staging / "extracted_test2" / "job-1"
    content_dir.mkdir(parents=True)
    (content_dir / "manifest.json").write_text(json.dumps(manifest))
    artifact = fake_staging / "job-2.tar"
    with tarfile.open(artifact, "w") as tf:
        tf.add(content_dir, arcname="job-1")

    import daemon.handlers_account as real_handlers_account
    import daemon.handlers_domain as real_handlers_domain
    import daemon.ols as real_ols

    monkeypatch.setattr(real_handlers_account, "reactivate_account", lambda params: {"status": "active"})
    monkeypatch.setattr(
        real_handlers_domain, "add_domain", lambda params: (_ for _ in ()).throw(RuntimeError(f"domain '{params['domain']}' is already in use"))
    )
    provisioned = []
    monkeypatch.setattr(real_ols, "provision_vhost", lambda account: provisioned.append(account.username))

    import os

    tmp_dir = str(fake_staging / "restore_tmp2")
    os.makedirs(tmp_dir)
    backup._restore_full("demo1", "terminated", str(artifact), tmp_dir, restore_job_id=1)

    assert provisioned == ["demo1"]


def test_restore_full_reapplies_docroot_permissions_for_preexisting_domain(isolated_db, fake_home, fake_mail_base, fake_staging, monkeypatch):
    """A real bug found live: a plain tar archive doesn't capture POSIX
    ACLs, so a domain restored via an already-existing Domain row (whose
    add_domain call short-circuited on "already in use", skipping
    ensure_docroot's ACL grant) left its docroot completely unreadable by
    OLS's own worker process -- a 404 with no obvious cause until getfacl
    showed the "nobody" ACL entry was simply never there. _restore_full
    must call ensure_docroot for every domain, not just newly-added ones."""
    with write_session() as session:
        account = make_account(session, status="terminated")
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot=f"{fake_home}/demo1/public_html"))

    manifest = {
        "username": "demo1", "php_version": "8.3", "quota_soft_mb": 1, "quota_hard_mb": 2,
        "cpu_pct": 10, "mem_mb": 20, "io_mb": 5, "pids_max": 5,
        "domains": [{"domain": "demo1.example", "kind": "primary", "docroot": f"{fake_home}/demo1/public_html"}],
        "databases": [], "mail_domains": [], "mail_users": [], "cron_jobs": [], "dns_zone": None,
    }
    content_dir = fake_staging / "extracted_test3" / "job-1"
    content_dir.mkdir(parents=True)
    (content_dir / "manifest.json").write_text(json.dumps(manifest))
    artifact = fake_staging / "job-3.tar"
    with tarfile.open(artifact, "w") as tf:
        tf.add(content_dir, arcname="job-1")

    import daemon.handlers_account as real_handlers_account
    import daemon.handlers_domain as real_handlers_domain
    import daemon.ols as real_ols

    monkeypatch.setattr(real_handlers_account, "reactivate_account", lambda params: {"status": "active"})
    monkeypatch.setattr(
        real_handlers_domain, "add_domain", lambda params: (_ for _ in ()).throw(RuntimeError(f"domain '{params['domain']}' is already in use"))
    )
    monkeypatch.setattr(real_ols, "provision_vhost", lambda account: None)
    ensured = []
    monkeypatch.setattr(real_handlers_domain, "ensure_docroot", lambda username, docroot: ensured.append((username, docroot)))

    import os

    tmp_dir = str(fake_staging / "restore_tmp3")
    os.makedirs(tmp_dir)
    backup._restore_full("demo1", "terminated", str(artifact), tmp_dir, restore_job_id=1)

    assert ensured == [("demo1", f"{fake_home}/demo1/public_html")]


def test_run_restore_job_database_end_to_end(isolated_db, tmp_path, fake_staging, monkeypatch):
    with write_session() as session:
        account = make_account(session)
    dest_path = tmp_path / "dest"
    dest_path.mkdir()
    dump = dest_path / "demo1_shop.sql.gz"
    with gzip.open(dump, "wt") as f:
        f.write("INSERT INTO t VALUES (1);")
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(dest_path)})

    monkeypatch.setattr(backup.mariadb, "database_exists", lambda name: True)
    ran = []

    def fake_run(args, input_text=None, timeout=30):
        ran.append(args)
        from daemon.procutil import ProcResult
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(backup, "run", fake_run)

    with write_session() as session:
        backup_job = BackupJob(account_id=account.id, kind="database", item_ref="demo1_shop", destination_id=dest["id"], status="completed", artifact_path=str(dump))
        session.add(backup_job)
        session.flush()
        backup_job_id = backup_job.id
        restore_job = RestoreJob(backup_job_id=backup_job_id, account_id=account.id, kind="database", item_ref="demo1_shop", status="pending")
        session.add(restore_job)
        session.flush()
        restore_job_id = restore_job.id

    backup._run_restore_job(restore_job_id)

    with write_session() as session:
        completed = session.get(RestoreJob, restore_job_id)
        assert completed.status == "completed"


def test_run_restore_job_marks_failed_on_error(isolated_db, tmp_path, fake_staging, monkeypatch):
    with write_session() as session:
        account = make_account(session)
    dest_path = tmp_path / "dest"
    dest_path.mkdir()
    artifact = dest_path / "x.sql.gz"
    with gzip.open(artifact, "wt") as f:
        f.write("x")
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(dest_path)})

    monkeypatch.setattr(backup.mariadb, "database_exists", lambda name: False)  # forces BackupError

    with write_session() as session:
        backup_job = BackupJob(account_id=account.id, kind="database", item_ref="demo1_shop", destination_id=dest["id"], status="completed", artifact_path=str(artifact))
        session.add(backup_job)
        session.flush()
        restore_job = RestoreJob(backup_job_id=backup_job.id, account_id=account.id, kind="database", item_ref="demo1_shop", status="pending")
        session.add(restore_job)
        session.flush()
        restore_job_id = restore_job.id

    backup._run_restore_job(restore_job_id)

    with write_session() as session:
        failed = session.get(RestoreJob, restore_job_id)
        assert failed.status == "failed"
        assert failed.error


# --- scheduler --------------------------------------------------------------------


def test_run_scheduled_backups_skips_when_not_due(isolated_db, tmp_path, monkeypatch):
    with write_session() as session:
        account = make_account(session)
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    backup.set_schedule({"frequency": "daily", "retention_count": 7, "destination_id": dest["id"]})

    with write_session() as session:
        session.add(
            BackupJob(
                account_id=account.id,
                kind="full",
                destination_id=dest["id"],
                status="completed",
                completed_at=backup.utcnow(),
            )
        )

    triggered = []
    monkeypatch.setattr(backup, "trigger_backup", lambda params: triggered.append(params))
    count = backup.run_scheduled_backups()
    assert count == 0
    assert triggered == []


def test_run_scheduled_backups_triggers_when_due(isolated_db, tmp_path, monkeypatch):
    with write_session() as session:
        account = make_account(session)
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    backup.set_schedule({"frequency": "daily", "retention_count": 7, "destination_id": dest["id"]})

    import datetime as dt

    with write_session() as session:
        session.add(
            BackupJob(
                account_id=account.id,
                kind="full",
                destination_id=dest["id"],
                status="completed",
                completed_at=backup.utcnow() - dt.timedelta(days=2),
            )
        )

    triggered = []
    monkeypatch.setattr(backup, "trigger_backup", lambda params: triggered.append(params))
    count = backup.run_scheduled_backups()
    assert count == 1
    assert triggered[0]["username"] == "demo1"
    assert triggered[0]["trigger"] == "scheduled"


def test_run_scheduled_backups_triggers_when_never_backed_up(isolated_db, tmp_path, monkeypatch):
    with write_session() as session:
        make_account(session)
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    backup.set_schedule({"frequency": "daily", "retention_count": 7, "destination_id": dest["id"]})

    triggered = []
    monkeypatch.setattr(backup, "trigger_backup", lambda params: triggered.append(params))
    count = backup.run_scheduled_backups()
    assert count == 1


def test_run_scheduled_backups_skips_disabled_schedule(isolated_db, tmp_path, monkeypatch):
    with write_session() as session:
        make_account(session)
    dest = backup.create_destination({"name": "d1", "kind": "local", "local_path": str(tmp_path / "d1")})
    backup.set_schedule({"frequency": "daily", "retention_count": 7, "destination_id": dest["id"], "enabled": False})

    triggered = []
    monkeypatch.setattr(backup, "trigger_backup", lambda params: triggered.append(params))
    count = backup.run_scheduled_backups()
    assert count == 0
