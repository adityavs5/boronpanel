"""Full-featured backup/restore system (Phase 2 feature 7).

Every backup is exactly one artifact file (tar, plain or gzipped
depending on kind) at a destination -- local path or an rclone remote --
giving a uniform model for storage, transfer, retention, and browsing
regardless of destination kind or backup kind (full/file/database/
mailbox). A "full" artifact bundles manifest.json (everything needed to
recreate the account's structure: domains, databases, mail, cron jobs,
DNS zone, resource limits) alongside home.tar.gz/databases/*.sql.gz/
mail/*.tar.gz -- the manifest is what makes granular browse/restore and
whole-account recreation from a single artifact possible.

Jobs run asynchronously in a small bounded thread pool (not the RPC
request thread) so a multi-minute backup/restore doesn't block
forgehostd's dispatch loop; BackupJob/RestoreJob rows are the only
progress-reporting channel the UI polls, updated at each stage.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import tarfile
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import (
    Account,
    BackupDestination,
    BackupJob,
    BackupSchedule,
    DatabaseGrant,
    Domain,
    DnsZone,
    MailDomain,
    MailUser,
    RestoreJob,
    utcnow,
)
from shared.validation import ValidationError, validate_domain, validate_username

from daemon import cron, mariadb, powerdns, rclone
from daemon.procutil import run

logger = logging.getLogger("forgehostd.backup")

_executor = ThreadPoolExecutor(max_workers=settings.backup_concurrency, thread_name_prefix="backup")

BACKUP_KINDS = ("full", "file", "database", "mailbox")
FREQUENCIES = ("daily", "weekly", "monthly")
FREQUENCY_SECONDS = {"daily": 86400, "weekly": 7 * 86400, "monthly": 30 * 86400}


class BackupError(Exception):
    pass


# --- destinations ---------------------------------------------------------


def _destination_to_dict(d: BackupDestination) -> dict:
    return {
        "id": d.id,
        "name": d.name,
        "kind": d.kind,
        "local_path": d.local_path,
        "rclone_remote": d.rclone_remote,
        "rclone_path_prefix": d.rclone_path_prefix,
        "created_at": d.created_at.isoformat() if d.created_at else None,
    }


def create_destination(params: dict) -> dict:
    name = params["name"].strip()
    if not name:
        raise ValidationError("destination name must not be empty")
    kind = params.get("kind", "local")
    if kind not in ("local", "rclone"):
        raise ValidationError("kind must be 'local' or 'rclone'")

    with write_session() as session:
        if session.scalar(select(BackupDestination).where(BackupDestination.name == name)) is not None:
            raise BackupError(f"destination '{name}' already exists")

    if kind == "local":
        local_path = params["local_path"]
        os.makedirs(local_path, exist_ok=True)
        with write_session() as session:
            dest = BackupDestination(name=name, kind="local", local_path=local_path)
            session.add(dest)
            session.flush()
            return _destination_to_dict(dest)

    # rclone
    remote_type = params["rclone_remote_type"]
    remote_config = params.get("rclone_config", {})
    remote_name = f"forgehost_{name}"
    rclone.create_remote(remote_name, remote_type, remote_config)
    with write_session() as session:
        dest = BackupDestination(
            name=name,
            kind="rclone",
            rclone_remote=remote_name,
            rclone_path_prefix=params.get("rclone_path_prefix", "").strip("/"),
        )
        session.add(dest)
        session.flush()
        return _destination_to_dict(dest)


def list_destinations(params: dict | None = None) -> dict:
    with write_session() as session:
        rows = session.scalars(select(BackupDestination).order_by(BackupDestination.name)).all()
        return {"destinations": [_destination_to_dict(d) for d in rows]}


def delete_destination(params: dict) -> dict:
    dest_id = int(params["id"])
    with write_session() as session:
        dest = session.get(BackupDestination, dest_id)
        if dest is None:
            raise BackupError(f"destination {dest_id} not found")
        in_use = session.scalar(select(BackupSchedule).where(BackupSchedule.destination_id == dest_id))
        if in_use is not None:
            raise BackupError("destination is referenced by a backup schedule; remove the schedule first")
        # BackupJob rows referencing this destination are historical
        # records (backup points still restorable from), never deleted
        # implicitly here -- same "never delete, keep history" pattern as
        # Account/Domain rows elsewhere in this project. A real bug found
        # live: without this check, the DELETE hit BackupJob's own foreign
        # key and surfaced a raw, unhelpful SQL IntegrityError instead of a
        # clean explanation.
        has_jobs = session.scalar(select(BackupJob.id).where(BackupJob.destination_id == dest_id).limit(1))
        if has_jobs is not None:
            raise BackupError(
                "destination has backup jobs recorded against it (kept as historical/restorable records); "
                "it cannot be deleted while any reference it"
            )
        remote_name = dest.rclone_remote
        session.delete(dest)
    if remote_name:
        rclone.delete_remote(remote_name)
    return {"id": dest_id, "status": "deleted"}


# --- schedules --------------------------------------------------------------


def _schedule_to_dict(s: BackupSchedule) -> dict:
    return {
        "id": s.id,
        "account_id": s.account_id,
        "frequency": s.frequency,
        "retention_count": s.retention_count,
        "destination_id": s.destination_id,
        "enabled": s.enabled,
    }


def set_schedule(params: dict) -> dict:
    username = params.get("username")
    frequency = params.get("frequency", "daily")
    if frequency not in FREQUENCIES:
        raise ValidationError(f"frequency must be one of {FREQUENCIES}")
    retention_count = int(params.get("retention_count", 7))
    if retention_count < 1:
        raise ValidationError("retention_count must be >= 1")
    destination_id = int(params["destination_id"])
    enabled = bool(params.get("enabled", True))

    account_id = None
    if username:
        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == username))
            if account is None:
                raise BackupError(f"account '{username}' not found")
            account_id = account.id

    with write_session() as session:
        if session.get(BackupDestination, destination_id) is None:
            raise BackupError(f"destination {destination_id} not found")
        existing = session.scalar(select(BackupSchedule).where(BackupSchedule.account_id == account_id))
        if existing is not None:
            existing.frequency = frequency
            existing.retention_count = retention_count
            existing.destination_id = destination_id
            existing.enabled = enabled
            session.flush()
            return _schedule_to_dict(existing)
        schedule = BackupSchedule(
            account_id=account_id,
            frequency=frequency,
            retention_count=retention_count,
            destination_id=destination_id,
            enabled=enabled,
        )
        session.add(schedule)
        session.flush()
        return _schedule_to_dict(schedule)


def get_effective_schedule(session, account_id: int) -> BackupSchedule | None:
    own = session.scalar(select(BackupSchedule).where(BackupSchedule.account_id == account_id))
    if own is not None:
        return own
    return session.scalar(select(BackupSchedule).where(BackupSchedule.account_id.is_(None)))


def list_schedules(params: dict | None = None) -> dict:
    with write_session() as session:
        rows = session.scalars(select(BackupSchedule)).all()
        return {"schedules": [_schedule_to_dict(s) for s in rows]}


# --- backup jobs -------------------------------------------------------------


def _job_to_dict(j: BackupJob) -> dict:
    return {
        "id": j.id,
        "account_id": j.account_id,
        "kind": j.kind,
        "item_ref": j.item_ref,
        "status": j.status,
        "trigger": j.trigger,
        "destination_id": j.destination_id,
        "artifact_path": j.artifact_path,
        "size_bytes": j.size_bytes,
        "progress_message": j.progress_message,
        "error": j.error,
        "started_at": j.started_at.isoformat() if j.started_at else None,
        "completed_at": j.completed_at.isoformat() if j.completed_at else None,
    }


def trigger_backup(params: dict) -> dict:
    username = validate_username(params["username"])
    kind = params.get("kind", "full")
    if kind not in BACKUP_KINDS:
        raise ValidationError(f"kind must be one of {BACKUP_KINDS}")
    item_ref = params.get("item_ref")
    if kind != "full" and not item_ref:
        raise ValidationError(f"item_ref is required for kind '{kind}'")
    trigger_source = params.get("trigger", "manual")

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise BackupError(f"account '{username}' not found")

        destination_id = params.get("destination_id")
        if destination_id is None:
            schedule = get_effective_schedule(session, account.id)
            if schedule is None:
                raise BackupError(
                    "no destination_id given and no backup schedule (per-account or server default) is configured"
                )
            destination_id = schedule.destination_id
        else:
            destination_id = int(destination_id)
        if session.get(BackupDestination, destination_id) is None:
            raise BackupError(f"destination {destination_id} not found")

        job = BackupJob(
            account_id=account.id,
            kind=kind,
            item_ref=item_ref,
            destination_id=destination_id,
            trigger=trigger_source,
            status="pending",
            progress_message="queued",
        )
        session.add(job)
        session.flush()
        job_id = job.id
        result = _job_to_dict(job)

    _executor.submit(_run_backup_job, job_id)
    return result


def get_job(params: dict) -> dict:
    job_id = int(params["job_id"])
    with write_session() as session:
        job = session.get(BackupJob, job_id)
        if job is None:
            raise BackupError(f"backup job {job_id} not found")
        return _job_to_dict(job)


def list_jobs(params: dict | None = None) -> dict:
    params = params or {}
    username = params.get("username")
    with write_session() as session:
        query = select(BackupJob).order_by(BackupJob.started_at.desc())
        if username:
            account = session.scalar(select(Account).where(Account.username == username))
            if account is None:
                raise BackupError(f"account '{username}' not found")
            query = query.where(BackupJob.account_id == account.id)
        rows = session.scalars(query.limit(int(params.get("limit", 100)))).all()
        return {"jobs": [_job_to_dict(j) for j in rows]}


def _update_job(job_id: int, **fields) -> None:
    with write_session() as session:
        job = session.get(BackupJob, job_id)
        if job is None:
            return
        for key, value in fields.items():
            setattr(job, key, value)


def _sanitize_ref(item_ref: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9_.@-]", "_", item_ref)[:100]


def _pwnam(username: str):
    import pwd

    return pwd.getpwnam(username)


def _as_aware_utc(value):
    import datetime as dt

    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value


def _dump_database(db_name: str, output_path: Path) -> None:
    cnf_path = _write_mysql_defaults_file()
    try:
        result = run(["mysqldump", f"--defaults-extra-file={cnf_path}", db_name], timeout=1800)
        if not result.ok:
            raise BackupError(f"mysqldump failed for '{db_name}': {result.stderr.strip()}")
        with gzip.open(output_path, "wt") as gz:
            gz.write(result.stdout)
    finally:
        os.unlink(cnf_path)


def _write_mysql_defaults_file() -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".cnf", delete=False) as f:
        f.write(
            f"[client]\nuser={settings.mariadb_admin_user}\n"
            f"password={settings.mariadb_admin_password}\nsocket={settings.mariadb_socket}\n"
        )
        cnf_path = f.name
    os.chmod(cnf_path, 0o600)
    return cnf_path


def _build_full_backup(username: str, staging_dir: Path, job_id: int) -> Path:
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        domains = session.scalars(select(Domain).where(Domain.account_id == account.id)).all()
        databases = session.scalars(select(DatabaseGrant).where(DatabaseGrant.account_id == account.id)).all()
        mail_domains = session.scalars(select(MailDomain).where(MailDomain.account_id == account.id)).all()
        mail_domain_ids = [m.id for m in mail_domains]
        mail_users = (
            session.scalars(select(MailUser).where(MailUser.mail_domain_id.in_(mail_domain_ids))).all()
            if mail_domain_ids
            else []
        )
        dns_zone_row = session.scalar(select(DnsZone).where(DnsZone.account_id == account.id))

        manifest = {
            "username": account.username,
            "backed_up_at": utcnow().isoformat(),
            "php_version": account.php_version,
            "quota_soft_mb": account.quota_soft_mb,
            "quota_hard_mb": account.quota_hard_mb,
            "cpu_pct": account.cpu_pct,
            "mem_mb": account.mem_mb,
            "io_mb": account.io_mb,
            "pids_max": account.pids_max,
            "domains": [{"domain": d.domain, "kind": d.kind, "docroot": d.docroot, "ssl_status": d.ssl_status} for d in domains],
            "databases": [{"db_name": db.db_name, "db_user": db.db_user} for db in databases],
            "mail_domains": [m.domain for m in mail_domains],
            "mail_users": [{"local_part": u.local_part, "domain": u.domain, "quota_mb": u.quota_mb} for u in mail_users],
            "cron_jobs": [],
            "dns_zone": None,
        }
        zone_name = dns_zone_row.zone if dns_zone_row is not None else None

    _update_job(job_id, progress_message="backing up cron jobs")
    try:
        manifest["cron_jobs"] = cron.list_jobs(username)
    except cron.CronError:
        manifest["cron_jobs"] = []

    if zone_name:
        _update_job(job_id, progress_message="backing up DNS zone")
        try:
            manifest["dns_zone"] = {"zone": zone_name, "records": powerdns.list_records(zone_name)}
        except powerdns.PowerDnsError:
            manifest["dns_zone"] = None

    (staging_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    _update_job(job_id, progress_message="backing up files (1/3)")
    home_dir = f"{settings.home_base}/{username}"
    if Path(home_dir).exists():
        result = run(["tar", "czf", str(staging_dir / "home.tar.gz"), "-C", settings.home_base, username], timeout=1800)
        if not result.ok:
            raise BackupError(f"tar of home directory failed: {result.stderr.strip()}")

    _update_job(job_id, progress_message="backing up databases (2/3)")
    if manifest["databases"]:
        db_dir = staging_dir / "databases"
        db_dir.mkdir()
        for db in manifest["databases"]:
            _dump_database(db["db_name"], db_dir / f"{db['db_name']}.sql.gz")

    _update_job(job_id, progress_message="backing up mail (3/3)")
    if manifest["mail_domains"]:
        mail_dir = staging_dir / "mail"
        mail_dir.mkdir()
        for domain in manifest["mail_domains"]:
            src = f"{settings.mail_base}/{domain}"
            if Path(src).exists():
                result = run(
                    ["tar", "czf", str(mail_dir / f"{domain}.tar.gz"), "-C", settings.mail_base, domain], timeout=1800
                )
                if not result.ok:
                    raise BackupError(f"tar of mail domain '{domain}' failed: {result.stderr.strip()}")

    artifact = staging_dir.parent / f"{staging_dir.name}.tar"
    result = run(["tar", "cf", str(artifact), "-C", str(staging_dir.parent), staging_dir.name], timeout=1800)
    if not result.ok:
        raise BackupError(f"final tar assembly failed: {result.stderr.strip()}")
    return artifact


def _build_file_backup(username: str, item_ref: str, staging_dir: Path, job_id: int) -> Path:
    home_dir = (Path(settings.home_base) / username).resolve()
    target = (home_dir / item_ref).resolve()
    try:
        target.relative_to(home_dir)
    except ValueError:
        raise BackupError("item_ref must be a path within the account's home directory") from None
    if not target.exists():
        raise BackupError(f"'{item_ref}' does not exist")
    _update_job(job_id, progress_message=f"backing up {item_ref}")
    artifact = staging_dir / "file.tar.gz"
    result = run(["tar", "czf", str(artifact), "-C", str(home_dir), item_ref], timeout=600)
    if not result.ok:
        raise BackupError(f"tar of '{item_ref}' failed: {result.stderr.strip()}")
    return artifact


def _build_database_backup(item_ref: str, staging_dir: Path, job_id: int) -> Path:
    _update_job(job_id, progress_message=f"dumping database {item_ref}")
    artifact = staging_dir / "database.sql.gz"
    _dump_database(item_ref, artifact)
    return artifact


def _build_mailbox_backup(item_ref: str, staging_dir: Path, job_id: int) -> Path:
    if "@" not in item_ref:
        raise BackupError("item_ref for a mailbox backup must be 'local_part@domain'")
    local_part, domain = item_ref.split("@", 1)
    validate_domain(domain)
    mail_dir = Path(settings.mail_base) / domain
    mailbox_dir = mail_dir / local_part
    if not mailbox_dir.exists():
        raise BackupError(f"mailbox '{item_ref}' not found")
    _update_job(job_id, progress_message=f"backing up mailbox {item_ref}")
    artifact = staging_dir / "mailbox.tar.gz"
    result = run(["tar", "czf", str(artifact), "-C", str(mail_dir), local_part], timeout=600)
    if not result.ok:
        raise BackupError(f"tar of mailbox '{item_ref}' failed: {result.stderr.strip()}")
    return artifact


def _run_backup_job(job_id: int) -> None:
    with write_session() as session:
        job = session.get(BackupJob, job_id)
        account = session.get(Account, job.account_id)
        destination = session.get(BackupDestination, job.destination_id)
        username = account.username
        kind = job.kind
        item_ref = job.item_ref
        dest_kind = destination.kind
        dest_local_path = destination.local_path
        dest_remote = destination.rclone_remote
        dest_prefix = destination.rclone_path_prefix

    _update_job(job_id, status="running", progress_message="starting")
    staging_dir = Path(settings.backup_staging_dir) / f"job-{job_id}"
    try:
        staging_dir.mkdir(parents=True, exist_ok=True)
        timestamp = utcnow().strftime("%Y%m%d-%H%M%S")

        if kind == "full":
            artifact_name = f"{username}_{timestamp}_full.tar"
            local_artifact = _build_full_backup(username, staging_dir, job_id)
        elif kind == "file":
            artifact_name = f"{username}_{timestamp}_file_{_sanitize_ref(item_ref)}.tar.gz"
            local_artifact = _build_file_backup(username, item_ref, staging_dir, job_id)
        elif kind == "database":
            artifact_name = f"{username}_{timestamp}_db_{_sanitize_ref(item_ref)}.sql.gz"
            local_artifact = _build_database_backup(item_ref, staging_dir, job_id)
        elif kind == "mailbox":
            artifact_name = f"{username}_{timestamp}_mailbox_{_sanitize_ref(item_ref)}.tar.gz"
            local_artifact = _build_mailbox_backup(item_ref, staging_dir, job_id)
        else:
            raise BackupError(f"unknown backup kind '{kind}'")

        size_bytes = local_artifact.stat().st_size
        _update_job(job_id, progress_message=f"uploading to destination ({size_bytes} bytes)")

        if dest_kind == "local":
            os.makedirs(dest_local_path, exist_ok=True)
            final_path = str(Path(dest_local_path) / artifact_name)
            shutil.move(str(local_artifact), final_path)
        else:
            final_path = (
                f"{dest_remote}:{dest_prefix}/{artifact_name}" if dest_prefix else f"{dest_remote}:{artifact_name}"
            )
            rclone.copy(str(local_artifact), final_path)

        _update_job(
            job_id,
            status="completed",
            artifact_path=final_path,
            size_bytes=size_bytes,
            progress_message="completed",
            completed_at=utcnow(),
        )
        _enforce_retention(job_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("backup job %d failed", job_id)
        _update_job(job_id, status="failed", error=str(exc), progress_message="failed", completed_at=utcnow())
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)


def _enforce_retention(job_id: int) -> None:
    with write_session() as session:
        job = session.get(BackupJob, job_id)
        account_id = job.account_id
        destination_id = job.destination_id
        schedule = get_effective_schedule(session, account_id)
        if schedule is None:
            return
        retention_count = schedule.retention_count
        completed = session.scalars(
            select(BackupJob)
            .where(
                BackupJob.account_id == account_id,
                BackupJob.destination_id == destination_id,
                BackupJob.kind == job.kind,
                BackupJob.status == "completed",
            )
            .order_by(BackupJob.completed_at.desc())
        ).all()
        to_delete = completed[retention_count:]
        destination = session.get(BackupDestination, destination_id)
        dest_kind = destination.kind
        victims = [(j.id, j.artifact_path) for j in to_delete]

    for old_job_id, artifact_path in victims:
        try:
            if dest_kind == "local":
                if artifact_path and Path(artifact_path).exists():
                    Path(artifact_path).unlink()
            elif artifact_path:
                rclone.delete_path(artifact_path)
        except Exception:  # noqa: BLE001
            logger.exception("failed to delete old backup artifact for retention (job %d)", old_job_id)
        with write_session() as session:
            old_job = session.get(BackupJob, old_job_id)
            if old_job is not None:
                session.delete(old_job)


def run_scheduled_backups() -> int:
    """Entry point for scripts/backup_scheduler.py (system cron). Triggers
    a full backup for every account whose effective (own or server-default)
    enabled schedule is due -- enough time elapsed since its last completed
    full backup for that schedule's frequency."""
    with write_session() as session:
        accounts = session.scalars(select(Account).where(Account.status.in_(["active", "suspended"]))).all()
        due = []
        for account in accounts:
            schedule = get_effective_schedule(session, account.id)
            if schedule is None or not schedule.enabled:
                continue
            last = session.scalar(
                select(BackupJob)
                .where(BackupJob.account_id == account.id, BackupJob.kind == "full", BackupJob.status == "completed")
                .order_by(BackupJob.completed_at.desc())
            )
            if last is not None and last.completed_at is not None:
                elapsed = (utcnow() - _as_aware_utc(last.completed_at)).total_seconds()
                if elapsed < FREQUENCY_SECONDS[schedule.frequency]:
                    continue
            due.append((account.username, schedule.destination_id))

    triggered = 0
    for username, destination_id in due:
        try:
            trigger_backup({"username": username, "kind": "full", "destination_id": destination_id, "trigger": "scheduled"})
            triggered += 1
        except Exception:  # noqa: BLE001
            logger.exception("failed to trigger scheduled backup for '%s'", username)
    return triggered


# --- browsing ----------------------------------------------------------------


def _fetch_artifact_locally(artifact_path: str, dest_kind: str, tmp_dir: str) -> str:
    if dest_kind == "local":
        return artifact_path
    local_copy = str(Path(tmp_dir) / Path(artifact_path.split(":", 1)[1]).name)
    rclone.copy_from_remote(artifact_path, local_copy)
    return local_copy


def browse_backup(params: dict) -> dict:
    job_id = int(params["job_id"])
    with write_session() as session:
        job = session.get(BackupJob, job_id)
        if job is None:
            raise BackupError(f"backup job {job_id} not found")
        if job.status != "completed":
            raise BackupError(f"backup job {job_id} is not completed (status: {job.status})")
        destination = session.get(BackupDestination, job.destination_id)
        kind = job.kind
        item_ref = job.item_ref
        artifact_path = job.artifact_path
        dest_kind = destination.kind

    if kind != "full":
        return {"kind": kind, "item_ref": item_ref}

    with tempfile.TemporaryDirectory(dir=settings.backup_staging_dir) as tmp:
        local_artifact = _fetch_artifact_locally(artifact_path, dest_kind, tmp)
        with tarfile.open(local_artifact) as tf:
            members = tf.getnames()
            manifest_member = next((m for m in members if m.endswith("manifest.json")), None)
            if manifest_member is None:
                raise BackupError("backup artifact has no manifest.json -- corrupt or not a full backup")
            manifest = json.loads(tf.extractfile(manifest_member).read())
        return {"kind": "full", "manifest": manifest, "contents": members}


# --- restore -------------------------------------------------------------------


def _restore_to_dict(r: RestoreJob) -> dict:
    return {
        "id": r.id,
        "backup_job_id": r.backup_job_id,
        "account_id": r.account_id,
        "kind": r.kind,
        "item_ref": r.item_ref,
        "status": r.status,
        "progress_message": r.progress_message,
        "error": r.error,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
    }


def trigger_restore(params: dict) -> dict:
    backup_job_id = int(params["backup_job_id"])
    item_ref = params.get("item_ref")

    with write_session() as session:
        backup_job = session.get(BackupJob, backup_job_id)
        if backup_job is None:
            raise BackupError(f"backup job {backup_job_id} not found")
        if backup_job.status != "completed":
            raise BackupError(f"backup job {backup_job_id} is not completed")
        account = session.get(Account, backup_job.account_id)

        effective_kind = params.get("kind") or backup_job.kind
        if effective_kind not in BACKUP_KINDS:
            raise ValidationError(f"kind must be one of {BACKUP_KINDS}")
        if backup_job.kind != "full" and effective_kind != backup_job.kind:
            raise BackupError("a non-full backup artifact can only restore its own kind")
        if backup_job.kind != "full" and not item_ref:
            item_ref = backup_job.item_ref

        restore_job = RestoreJob(
            backup_job_id=backup_job_id,
            account_id=account.id,
            kind=effective_kind,
            item_ref=item_ref,
            status="pending",
            progress_message="queued",
        )
        session.add(restore_job)
        session.flush()
        restore_job_id = restore_job.id
        result = _restore_to_dict(restore_job)

    _executor.submit(_run_restore_job, restore_job_id)
    return result


def get_restore_job(params: dict) -> dict:
    restore_job_id = int(params["restore_job_id"])
    with write_session() as session:
        r = session.get(RestoreJob, restore_job_id)
        if r is None:
            raise BackupError(f"restore job {restore_job_id} not found")
        return _restore_to_dict(r)


def list_restore_jobs(params: dict | None = None) -> dict:
    params = params or {}
    username = params.get("username")
    with write_session() as session:
        query = select(RestoreJob).order_by(RestoreJob.started_at.desc())
        if username:
            account = session.scalar(select(Account).where(Account.username == username))
            if account is None:
                raise BackupError(f"account '{username}' not found")
            query = query.where(RestoreJob.account_id == account.id)
        rows = session.scalars(query.limit(int(params.get("limit", 100)))).all()
        return {"restore_jobs": [_restore_to_dict(r) for r in rows]}


def _update_restore(restore_job_id: int, **fields) -> None:
    with write_session() as session:
        r = session.get(RestoreJob, restore_job_id)
        if r is None:
            return
        for key, value in fields.items():
            setattr(r, key, value)


def _restore_database_dump(db_name: str, dump_path: Path) -> None:
    if not mariadb.database_exists(db_name):
        raise BackupError(f"database '{db_name}' does not exist -- create it (db.create) before restoring into it")
    cnf_path = _write_mysql_defaults_file()
    try:
        with gzip.open(dump_path, "rt") as gz:
            sql = gz.read()
        result = run(["mysql", f"--defaults-extra-file={cnf_path}", db_name], input_text=sql, timeout=1800)
        if not result.ok:
            raise BackupError(f"mysql restore failed for '{db_name}': {result.stderr.strip()}")
    finally:
        os.unlink(cnf_path)


def _restore_full(username: str, account_status: str, local_artifact: str, tmp_dir: str, restore_job_id: int) -> None:
    _update_restore(restore_job_id, progress_message="extracting backup")
    extract_dir = Path(tmp_dir) / "extracted"
    extract_dir.mkdir()
    with tarfile.open(local_artifact) as tf:
        tf.extractall(extract_dir)

    inner_dirs = [d for d in extract_dir.iterdir() if d.is_dir()]
    if len(inner_dirs) != 1:
        raise BackupError("unexpected full backup artifact layout")
    content_dir = inner_dirs[0]

    manifest_path = content_dir / "manifest.json"
    if not manifest_path.exists():
        raise BackupError("backup artifact has no manifest.json")
    manifest = json.loads(manifest_path.read_text())

    from daemon import handlers_account, handlers_cron, handlers_database, handlers_dns, handlers_domain, handlers_mail

    if account_status in ("terminated", "error"):
        # reactivate_account, not create_account: terminating an account
        # never deletes its historical DB row (see terminate_account), so
        # create_account's own "already exists" guard would always fire
        # here -- found live when that got silently treated as a harmless
        # no-op, the Linux user was never actually recreated, and the
        # restore failed several steps later trying to chown files to a
        # uid that no longer existed.
        _update_restore(restore_job_id, progress_message="recreating account")
        handlers_account.reactivate_account(
            {
                "username": username,
                "php_version": manifest.get("php_version"),
                "quota_soft_mb": manifest.get("quota_soft_mb"),
                "quota_hard_mb": manifest.get("quota_hard_mb"),
                "cpu_pct": manifest.get("cpu_pct"),
                "mem_mb": manifest.get("mem_mb"),
                "io_mb": manifest.get("io_mb"),
                "pids_max": manifest.get("pids_max"),
            }
        )

        _update_restore(restore_job_id, progress_message="recreating domains")
        for d in manifest.get("domains", []):
            try:
                handlers_domain.add_domain({"username": username, "domain": d["domain"], "kind": d["kind"]})
            except RuntimeError:
                pass  # Domain row already exists (termination never deletes it, same as
                # the account row) -- not fatal, but neither its docroot directory nor
                # its OLS vhost survived termination, and add_domain's own
                # ensure_docroot()/provision_vhost() side effects never ran for it
                # (its early "already in use" exit skips both). Handled explicitly
                # further down, after files are restored -- provisioning the vhost
                # needs the docroot to actually exist first (OLS's own `-t` validates
                # DocRoot is accessible), which for an already-existing Domain row only
                # becomes true once home.tar.gz is extracted.

        _update_restore(restore_job_id, progress_message="recreating databases")
        for db in manifest.get("databases", []):
            try:
                suffix = db["db_name"][len(username) + 1 :] if db["db_name"].startswith(f"{username}_") else db["db_name"]
                handlers_database.create_database({"username": username, "name": suffix})
            except RuntimeError:
                pass

        _update_restore(restore_job_id, progress_message="recreating mail")
        for domain in manifest.get("mail_domains", []):
            try:
                handlers_mail.create_mail_domain({"username": username, "domain": domain})
            except RuntimeError:
                pass
        # Mailbox passwords are never stored in a backup (correctly -- a
        # backup manifest holding plaintext mail passwords would be a real
        # secret-handling regression). A terminated account's original
        # passwords are unrecoverable, so each recreated mailbox gets a
        # fresh random one; the admin must reset the ones customers need
        # via mail.change_password after restore. Noted in the final
        # progress message rather than silently leaving this undiscoverable.
        reset_mailboxes = []
        for mu in manifest.get("mail_users", []):
            try:
                handlers_mail.create_mailbox(
                    {
                        "domain": mu["domain"],
                        "local_part": mu["local_part"],
                        "password": mariadb.generate_password(),
                        "quota_mb": mu.get("quota_mb"),
                    }
                )
                reset_mailboxes.append(f"{mu['local_part']}@{mu['domain']}")
            except RuntimeError:
                pass
        if reset_mailboxes:
            _update_restore(
                restore_job_id,
                progress_message=(
                    f"recreated {len(reset_mailboxes)} mailbox(es) with new random passwords "
                    f"(original passwords are never stored in backups) -- reset via mail.change_password"
                ),
            )

        _update_restore(restore_job_id, progress_message="recreating cron jobs")
        for cj in manifest.get("cron_jobs", []):
            try:
                handlers_cron.add_cron_job(
                    {"username": username, "schedule": cj["schedule"], "command": cj["command"], "label": cj.get("label", "")}
                )
            except Exception:  # noqa: BLE001
                pass

        dns_zone = manifest.get("dns_zone")
        if dns_zone:
            _update_restore(restore_job_id, progress_message="recreating DNS zone")
            try:
                handlers_dns.create_zone({"username": username, "domain": dns_zone["zone"]})
            except RuntimeError:
                pass
            for rec in dns_zone.get("records", []):
                try:
                    handlers_dns.set_record(
                        {
                            "domain": dns_zone["zone"],
                            "subdomain": rec.get("name", "@"),
                            "type": rec["type"],
                            "values": rec["values"],
                            "ttl": rec.get("ttl", 3600),
                        }
                    )
                except Exception:  # noqa: BLE001
                    pass

    _update_restore(restore_job_id, progress_message="restoring files")
    home_tar = content_dir / "home.tar.gz"
    if home_tar.exists():
        result = run(["tar", "xzf", str(home_tar), "-C", settings.home_base], timeout=1800)
        if not result.ok:
            raise BackupError(f"failed to restore home directory: {result.stderr.strip()}")
        pw = _pwnam(username)
        run(["chown", "-R", f"{pw.pw_uid}:{pw.pw_gid}", f"{settings.home_base}/{username}"], timeout=600)

    if account_status in ("terminated", "error"):
        # Now that home.tar.gz has actually been extracted, redo the parts
        # of add_domain's own success path that its early "already in use"
        # exit skipped for every domain whose row survived termination:
        # the docroot's ownership/permissions and the "nobody" POSIX ACL
        # grant OLS's worker process needs to read it at all (found live --
        # a plain tar archive doesn't capture ACLs, so a restored docroot
        # was otherwise unreadable by OLS even with a correctly configured
        # vhost pointed right at it, producing a 404 with no obvious cause
        # until getfacl showed the grant was simply never there). Then
        # (re)provision every domain-vhost, unconditionally regardless of
        # whether its Domain row was just newly created above or already
        # existed from before termination -- either way its OLS vhost is
        # currently missing.
        _update_restore(restore_job_id, progress_message="restoring docroot permissions")
        for d in manifest.get("domains", []):
            try:
                handlers_domain.ensure_docroot(username, d["docroot"])
            except Exception:  # noqa: BLE001
                logger.exception("failed to restore docroot permissions for '%s' (account '%s')", d["domain"], username)

        from daemon import ols

        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == username))
            account_snapshot = account
        try:
            ols.provision_vhost(account_snapshot)
        except Exception:  # noqa: BLE001
            logger.exception("failed to (re)provision vhosts for restored account '%s'", username)

    _update_restore(restore_job_id, progress_message="restoring databases")
    db_dir = content_dir / "databases"
    if db_dir.exists():
        for dump in db_dir.glob("*.sql.gz"):
            db_name = dump.name[: -len(".sql.gz")]
            _restore_database_dump(db_name, dump)

    _update_restore(restore_job_id, progress_message="restoring mail")
    mail_dir = content_dir / "mail"
    if mail_dir.exists():
        for tarball in mail_dir.glob("*.tar.gz"):
            domain = tarball.name[: -len(".tar.gz")]
            result = run(["tar", "xzf", str(tarball), "-C", settings.mail_base], timeout=1800)
            if not result.ok:
                raise BackupError(f"failed to restore mail domain '{domain}': {result.stderr.strip()}")
            run(["chown", "-R", "vmail:vmail", f"{settings.mail_base}/{domain}"], timeout=600)


def _restore_file(username: str, item_ref: str, local_artifact: str, restore_job_id: int) -> None:
    _update_restore(restore_job_id, progress_message=f"restoring {item_ref}")
    home_dir = Path(settings.home_base) / username
    if not home_dir.exists():
        raise BackupError(f"account '{username}' has no home directory to restore into")
    result = run(["tar", "xzf", local_artifact, "-C", str(home_dir)], timeout=600)
    if not result.ok:
        raise BackupError(f"failed to restore file '{item_ref}': {result.stderr.strip()}")
    pw = _pwnam(username)
    run(["chown", "-R", f"{pw.pw_uid}:{pw.pw_gid}", str(home_dir / item_ref)], timeout=120)


def _restore_database(item_ref: str, local_artifact: str, restore_job_id: int) -> None:
    _update_restore(restore_job_id, progress_message=f"restoring database {item_ref}")
    _restore_database_dump(item_ref, Path(local_artifact))


def _restore_mailbox(item_ref: str, local_artifact: str, restore_job_id: int) -> None:
    if "@" not in item_ref:
        raise BackupError("item_ref for a mailbox restore must be 'local_part@domain'")
    local_part, domain = item_ref.split("@", 1)
    _update_restore(restore_job_id, progress_message=f"restoring mailbox {item_ref}")
    mail_dir = Path(settings.mail_base) / domain
    if not mail_dir.exists():
        raise BackupError(f"mail domain '{domain}' does not exist -- create it before restoring a mailbox into it")
    result = run(["tar", "xzf", local_artifact, "-C", str(mail_dir)], timeout=600)
    if not result.ok:
        raise BackupError(f"failed to restore mailbox '{item_ref}': {result.stderr.strip()}")
    run(["chown", "-R", "vmail:vmail", str(mail_dir / local_part)], timeout=120)


def _run_restore_job(restore_job_id: int) -> None:
    with write_session() as session:
        restore_job = session.get(RestoreJob, restore_job_id)
        backup_job = session.get(BackupJob, restore_job.backup_job_id)
        destination = session.get(BackupDestination, backup_job.destination_id)
        account = session.get(Account, restore_job.account_id)
        username = account.username
        account_status = account.status
        kind = restore_job.kind
        item_ref = restore_job.item_ref
        artifact_path = backup_job.artifact_path
        dest_kind = destination.kind

    _update_restore(restore_job_id, status="running", progress_message="fetching backup artifact")
    tmp_dir = tempfile.mkdtemp(dir=settings.backup_staging_dir, prefix=f"restore-{restore_job_id}-")
    try:
        local_artifact = _fetch_artifact_locally(artifact_path, dest_kind, tmp_dir)

        if kind == "full":
            _restore_full(username, account_status, local_artifact, tmp_dir, restore_job_id)
        elif kind == "file":
            _restore_file(username, item_ref, local_artifact, restore_job_id)
        elif kind == "database":
            _restore_database(item_ref, local_artifact, restore_job_id)
        elif kind == "mailbox":
            _restore_mailbox(item_ref, local_artifact, restore_job_id)
        else:
            raise BackupError(f"unknown restore kind '{kind}'")

        _update_restore(restore_job_id, status="completed", progress_message="completed", completed_at=utcnow())
    except Exception as exc:  # noqa: BLE001
        logger.exception("restore job %d failed", restore_job_id)
        _update_restore(restore_job_id, status="failed", error=str(exc), progress_message="failed", completed_at=utcnow())
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
