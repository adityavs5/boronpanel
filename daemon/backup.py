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
borond's dispatch loop; BackupJob/RestoreJob rows are the only
progress-reporting channel the UI polls, updated at each stage.
"""
from __future__ import annotations

from daemon.database_operations import serialized_worker
import gzip
import hashlib
import json
import logging
import os
import stat
import subprocess
import secrets
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
    SnapshotRun,
    SnapshotRestore,
    utcnow,
)
from shared.validation import ValidationError, validate_domain, validate_username, validate_mailbox_local_part

from daemon import cron, dnsprovider, events, mariadb, rclone
from daemon.procutil import run

logger = logging.getLogger("borond.backup")

_executor = ThreadPoolExecutor(max_workers=settings.backup_concurrency, thread_name_prefix="backup")

BACKUP_KINDS = ("full", "file", "database", "databases", "mailbox")
FREQUENCIES = ("daily", "weekly", "monthly")
FREQUENCY_SECONDS = {"daily": 86400, "weekly": 7 * 86400, "monthly": 30 * 86400}
PORTABLE_ARCHIVE_FORMAT = "boron-account-archive"
PORTABLE_ARCHIVE_VERSION = 1
MAX_BACKUP_ARCHIVE_MEMBERS = 200_000
MAX_BACKUP_ARCHIVE_PATH_BYTES = 2048
MAX_BACKUP_ARCHIVE_PATH_DEPTH = 64


class BackupError(Exception):
    pass


def _safe_extract_tar(archive_path: str | Path, destination: str | Path) -> None:
    """Extract a backup component with traversal and expansion limits."""
    try:
        with tarfile.open(archive_path) as archive:
            total = 0
            count = 0
            file_names: set[str] = set()
            for member in archive:
                count += 1
                if count > MAX_BACKUP_ARCHIVE_MEMBERS:
                    raise BackupError("archive component contains too many members")
                if (len(member.name.encode("utf-8", errors="replace")) > MAX_BACKUP_ARCHIVE_PATH_BYTES
                        or len(Path(member.name).parts) > MAX_BACKUP_ARCHIVE_PATH_DEPTH):
                    raise BackupError("archive component contains an excessively long or deep path")
                if member.isreg():
                    if member.name in file_names:
                        raise BackupError("archive component contains a duplicate file path")
                    file_names.add(member.name)
                    total += member.size
                    if total > settings.cpanel_import_max_extracted_bytes:
                        raise BackupError(
                            f"archive component expands beyond the {settings.cpanel_import_max_extracted_bytes} byte limit"
                        )
            archive.extractall(destination, filter="data")
    except tarfile.TarError as exc:
        raise BackupError(f"unsafe or unreadable archive component: {exc}") from exc


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
    remote_name = f"boron_{name}"
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
    from daemon.snapshot_jobs import lock
    with lock('queue'):
        return _trigger_backup_locked(params)


def _authorize_item(session, account, kind, item_ref):
    if kind in ("full", "databases"):
        return
    if not isinstance(item_ref, str) or not item_ref:
        raise BackupError("a backup item is required")
    if kind == "database":
        if session.scalar(select(DatabaseGrant.id).where(
                DatabaseGrant.account_id == account.id, DatabaseGrant.db_name == item_ref)) is None:
            raise BackupError("choose a database owned by this account")
    elif kind == "mailbox":
        local, separator, domain = item_ref.partition("@")
        if not separator:
            raise BackupError("mailbox must be local_part@domain")
        validate_mailbox_local_part(local)
        validate_domain(domain)
        if session.scalar(select(MailUser.id).join(MailDomain, MailDomain.id == MailUser.mail_domain_id).where(
                MailDomain.account_id == account.id, MailUser.domain == domain, MailUser.local_part == local)) is None:
            raise BackupError("choose a mailbox owned by this account")
    elif kind == "file":
        path = Path(item_ref)
        if path.is_absolute() or '..' in path.parts or item_ref.startswith('-') or '\x00' in item_ref:
            raise BackupError("file path must stay inside the account home")
    else:
        raise BackupError("unsupported backup kind")


def _trigger_backup_locked(params: dict) -> dict:
    username = validate_username(params["username"])
    kind = params.get("kind", "full")
    if kind not in BACKUP_KINDS:
        raise ValidationError(f"kind must be one of {BACKUP_KINDS}")
    item_ref = params.get("item_ref")
    if kind not in ("full", "databases") and not item_ref:
        raise ValidationError(f"item_ref is required for kind '{kind}'")
    trigger_source = params.get("trigger", "manual")

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise BackupError(f"account '{username}' not found")

        _authorize_item(session, account, kind, item_ref)

        owned_databases = set(session.scalars(select(DatabaseGrant.db_name).where(DatabaseGrant.account_id == account.id)).all())
        if kind == "database" and item_ref not in owned_databases:
            raise BackupError("choose a database owned by this account")
        if kind == "databases" and not owned_databases:
            raise BackupError("this account has no databases to back up")

        for model in (SnapshotRun, SnapshotRestore, RestoreJob):
            if session.scalar(select(model.id).where(model.account_id == account.id, model.status.in_(("pending", "running")))):
                raise BackupError("a backup or restore is already in progress for this account")

        # Security audit finding F9: the scheduled path is safely bounded
        # (frequency is an enum -- daily/weekly/monthly, never a raw cron
        # expression), but manual triggers had no cooldown at all, and
        # backup artifacts live under settings.backup_staging_dir/the
        # configured destination -- outside the account's own jailed home,
        # so its disk quota never capped this. A customer spamming
        # backup.job.trigger could queue unboundedly in the shared,
        # bounded-concurrency executor and fill shared backup storage,
        # starving every other account's real backups behind the spam.
        existing_active = session.scalar(
            select(BackupJob).where(
                BackupJob.account_id == account.id,
                BackupJob.status.in_(("pending", "running")),
            )
        )
        if existing_active is not None:
            raise BackupError(
                f"a backup is already in progress for this account (job {existing_active.id}, status '{existing_active.status}')"
            )

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


def _resolve_account_id(username: str) -> int:
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        return account.id


def get_job(params: dict) -> dict:
    """job_id is a small sequential integer, not a capability -- see
    daemon/wordpress.py's get_job docstring for the exact same class of
    bug (found auditing this module right after finding it there;
    docs/CHECKPOINT-phase4-0b-cross-account-idor.md). username is required and
    cross-checked against the job's own account_id; a mismatch is
    reported identically to a nonexistent job_id."""
    job_id = int(params["job_id"])
    account_id = _resolve_account_id(validate_username(params["username"]))
    with write_session() as session:
        job = session.get(BackupJob, job_id)
        if job is None or job.account_id != account_id:
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


def _build_user_tar(username, output, directory, item):
    owner = _pwnam(username)
    if owner.pw_uid <= 0 or owner.pw_gid <= 0:
        raise BackupError("refusing file backup as root")
    # Only the root-owned output descriptor crosses the privilege boundary.
    # Mutable source paths are opened by the hosting identity itself.
    with open(output, 'xb') as archive, tempfile.TemporaryFile() as errors:
        os.fchmod(archive.fileno(), 0o600)
        result = subprocess.run(['tar','czf','-','-C',str(directory),'--',item],
            stdout=archive, stderr=errors, timeout=1800, user=owner.pw_uid,
            group=owner.pw_gid, extra_groups=())
        if result.returncode:
            raise BackupError("account-privileged file backup failed")


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


def _write_mysql_defaults_file(user: str | None = None, password: str | None = None) -> str:
    if user is None:
        user = settings.mariadb_admin_user
    if password is None:
        password = settings.mariadb_admin_password
    # MariaDB option-file values need quoting, even for generated passwords.
    def option(value: str) -> str:
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'

    with tempfile.NamedTemporaryFile(mode="w", suffix=".cnf", delete=False) as f:
        f.write(
            f"[client]\nuser={option(user)}\n"
            f"password={option(password)}\nsocket={option(settings.mariadb_socket)}\n"
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
            "format": PORTABLE_ARCHIVE_FORMAT,
            "format_version": PORTABLE_ARCHIVE_VERSION,
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
            # Provider-agnostic: dnsprovider routes to PowerDNS or Cloudflare
            # per zone; both return the same generic record dicts.
            manifest["dns_zone"] = {"zone": zone_name, "records": dnsprovider.list_records(zone_name)}
        except dnsprovider.DnsError:
            manifest["dns_zone"] = None

    _update_job(job_id, progress_message="backing up files (1/3)")
    home_dir = f"{settings.home_base}/{username}"
    if Path(home_dir).exists():
        _build_user_tar(username, staging_dir / "home.tar.gz", settings.home_base, username)

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

    # Component checksums make the artifact independently verifiable after it
    # has crossed servers or storage providers. The manifest itself is not in
    # this list (that would be self-referential).
    components = []
    for path in sorted(p for p in staging_dir.rglob("*") if p.is_file()):
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        components.append({
            "path": path.relative_to(staging_dir).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": digest.hexdigest(),
        })
    manifest["components"] = components
    (staging_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

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
    _build_user_tar(username, artifact, home_dir, item_ref)
    return artifact


def _build_database_backup(item_ref: str, staging_dir: Path, job_id: int) -> Path:
    _update_job(job_id, progress_message=f"dumping database {item_ref}")
    artifact = staging_dir / "database.sql.gz"
    _dump_database(item_ref, artifact)
    return artifact


def _build_databases_backup(account_id: int, staging_dir: Path, job_id: int) -> Path:
    with write_session() as session:
        names = list(session.scalars(select(DatabaseGrant.db_name).where(DatabaseGrant.account_id == account_id)).all())
    if not names:
        raise BackupError("this account has no databases to back up")
    content = staging_dir / "databases"
    content.mkdir(parents=True, exist_ok=True)
    for index, name in enumerate(names, start=1):
        _update_job(job_id, progress_message=f"dumping database {index} of {len(names)}: {name}")
        _dump_database(name, content / f"{name}.sql.gz")
    (staging_dir / "manifest.json").write_text(json.dumps({"kind": "databases", "databases": names}, indent=2) + "\n")
    artifact = staging_dir.parent / f"{staging_dir.name}-databases.tar"
    result = run(["tar", "cf", str(artifact), "-C", str(staging_dir), "manifest.json", "databases"], timeout=1800)
    if not result.ok:
        raise BackupError(f"database archive assembly failed: {result.stderr.strip()}")
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
    staging_dir = Path(settings.backup_staging_dir) / f"job-{job_id}"
    kind = None
    account_snapshot = None
    try:
        with write_session() as session:
            job = session.get(BackupJob, job_id)
            if job is None or job.status != "pending":
                return
            account = session.get(Account, job.account_id)
            if account is None:
                raise BackupError("backup account no longer exists")
            if account.status != "active":
                raise BackupError(f"cannot run backup for an account in status '{account.status}'")
            destination = session.get(BackupDestination, job.destination_id)
            if destination is None:
                raise BackupError("backup destination no longer exists")
            username = account.username
            kind = job.kind
            item_ref = job.item_ref
            _authorize_item(session, account, kind, item_ref)
            dest_kind = destination.kind
            dest_local_path = destination.local_path
            dest_remote = destination.rclone_remote
            dest_prefix = destination.rclone_path_prefix
            session.expunge(account)
            account_snapshot = account
            job.status = "running"
            job.progress_message = "starting"

        staging_dir.mkdir(parents=True, exist_ok=True)
        timestamp = utcnow().strftime("%Y%m%d-%H%M%S")

        if kind == "full":
            artifact_name = f"{username}_{timestamp}.boron.tar"
            local_artifact = _build_full_backup(username, staging_dir, job_id)
        elif kind == "file":
            artifact_name = f"{username}_{timestamp}_file_{_sanitize_ref(item_ref)}.tar.gz"
            local_artifact = _build_file_backup(username, item_ref, staging_dir, job_id)
        elif kind == "database":
            artifact_name = f"{username}_{timestamp}_db_{_sanitize_ref(item_ref)}.sql.gz"
            local_artifact = _build_database_backup(item_ref, staging_dir, job_id)
        elif kind == "databases":
            artifact_name = f"{username}_{timestamp}_all_databases.tar"
            local_artifact = _build_databases_backup(account_snapshot.id, staging_dir, job_id)
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
        # Phase 7b features 3/4: "backup completed" notification/webhook.
        # Only "manual"/"scheduled" full-account backups fire this -- a
        # granular file/database/mailbox backup is a much more routine
        # action (e.g. before a risky change) that this project's own
        # goal text doesn't ask to notify on, and would otherwise make the
        # channel noisy for an account with frequent granular backups.
        if kind == "full":
            events.emit("backup.completed", account_snapshot, job_id=job_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("backup job %d failed", job_id)
        _update_job(job_id, status="failed", error=str(exc), progress_message="failed", completed_at=utcnow())
        if kind == "full" and account_snapshot is not None:
            events.emit("backup.failed", account_snapshot, job_id=job_id, error=str(exc))
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
    account_id = _resolve_account_id(validate_username(params["username"]))
    with write_session() as session:
        job = session.get(BackupJob, job_id)
        if job is None or job.account_id != account_id:
            raise BackupError(f"backup job {job_id} not found")
        if job.status != "completed":
            raise BackupError(f"backup job {job_id} is not completed (status: {job.status})")
        destination = session.get(BackupDestination, job.destination_id)
        kind = job.kind
        item_ref = job.item_ref
        artifact_path = job.artifact_path
        dest_kind = destination.kind

    if kind not in ("full", "databases"):
        return {"kind": kind, "item_ref": item_ref}

    with tempfile.TemporaryDirectory(dir=settings.backup_staging_dir) as tmp:
        local_artifact = _fetch_artifact_locally(artifact_path, dest_kind, tmp)
        with tarfile.open(local_artifact) as tf:
            members = tf.getnames()
            manifest_member = next((m for m in members if m.endswith("manifest.json")), None)
            if manifest_member is None:
                raise BackupError("backup artifact has no manifest.json -- corrupt backup")
            manifest = json.loads(tf.extractfile(manifest_member).read())
        return {"kind": kind, "manifest": manifest, "contents": members}


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
    from daemon.snapshot_jobs import lock
    with lock('queue'):
        return _trigger_restore_locked(params)


def _trigger_restore_locked(params: dict) -> dict:
    backup_job_id = int(params["backup_job_id"])
    item_ref = params.get("item_ref")
    account_id = _resolve_account_id(validate_username(params["username"]))

    with write_session() as session:
        backup_job = session.get(BackupJob, backup_job_id)
        if backup_job is None or backup_job.account_id != account_id:
            raise BackupError(f"backup job {backup_job_id} not found")
        if backup_job.status != "completed":
            raise BackupError(f"backup job {backup_job_id} is not completed")
        account = session.get(Account, backup_job.account_id)

        for model in (SnapshotRun, SnapshotRestore, BackupJob):
            if session.scalar(select(model.id).where(model.account_id == account.id, model.status.in_(("pending", "running")))):
                raise BackupError("a backup or restore is already in progress for this account")

        effective_kind = params.get("kind") or backup_job.kind
        if effective_kind not in BACKUP_KINDS:
            raise ValidationError(f"kind must be one of {BACKUP_KINDS}")
        if backup_job.kind != "full" and effective_kind != backup_job.kind:
            raise BackupError("a non-full backup artifact can only restore its own kind")
        if backup_job.kind not in ("full", "databases") and not item_ref:
            item_ref = backup_job.item_ref
        _authorize_item(session, account, effective_kind, item_ref)

        # Same guard trigger_backup applies (F9): a restore rewrites the
        # account's home dir and databases in place, so two concurrent restores
        # for one account would race on the same files/DBs and corrupt the
        # result. Refuse to queue a second while one is pending/running.
        existing_active = session.scalar(
            select(RestoreJob).where(
                RestoreJob.account_id == account.id,
                RestoreJob.status.in_(("pending", "running")),
            )
        )
        if existing_active is not None:
            raise BackupError(
                f"a restore is already in progress for this account (job {existing_active.id}, status '{existing_active.status}')"
            )

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
    """Not currently wired to any API route, but fixed with the same
    username-ownership check as get_job/browse_backup/trigger_restore
    above so it can't become a silent IDOR the moment a future route
    exposes it (the exact "not built yet, so not a bug yet" trap this
    project's own review process explicitly watches for)."""
    restore_job_id = int(params["restore_job_id"])
    account_id = _resolve_account_id(validate_username(params["username"]))
    with write_session() as session:
        r = session.get(RestoreJob, restore_job_id)
        if r is None or r.account_id != account_id:
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


@serialized_worker
def _restore_database_dump(db_name: str, dump_path: Path) -> None:
    if not mariadb.database_exists(db_name):
        raise BackupError(f"database '{db_name}' does not exist -- create it (db.create) before restoring into it")
    # An imported or remotely stored backup is untrusted. SQL is executed
    # with a one-use MariaDB principal scoped to exactly this database, never
    # the provisioning principal. The client also runs in binary batch mode
    # so dump text cannot invoke its own shell/source commands as root.
    restore_user = "br_restore_" + secrets.token_hex(8)
    restore_password = mariadb.generate_password()
    mariadb.create_db_user(restore_user, restore_password)
    cnf_path = None
    sql_path = None
    try:
        mariadb.grant_exact_database(db_name, restore_user)
        cnf_path = _write_mysql_defaults_file(restore_user, restore_password)
        # A .sql.gz can expand far beyond its outer archive's declared size.
        # Stream to a private file under a byte ceiling, then pipe it to the
        # client without ever holding the whole SQL dump in daemon memory.
        with tempfile.NamedTemporaryFile(mode="wb", prefix="boron-db-restore-", suffix=".sql", delete=False) as expanded:
            sql_path = expanded.name
            written = 0
            with gzip.open(dump_path, "rb") as compressed:
                while chunk := compressed.read(1024 * 1024):
                    written += len(chunk)
                    if written > settings.cpanel_import_max_extracted_bytes:
                        raise BackupError("database dump exceeds the restore expansion limit")
                    expanded.write(chunk)
        # --defaults-file is exclusive. --defaults-extra-file still reads
        # later user option files, which can silently override the scoped
        # credentials when this root daemon invokes the client.
        result = run(["mysql", f"--defaults-file={cnf_path}", "--binary-mode", "--local-infile=0", db_name], input_path=sql_path, discard_stdout=True, timeout=1800)
        if not result.ok:
            raise BackupError(f"mysql restore failed for '{db_name}'")
    finally:
        if sql_path is not None:
            os.unlink(sql_path)
        if cnf_path is not None:
            os.unlink(cnf_path)
        mariadb.drop_db_user(restore_user)


@serialized_worker
def _restore_full(
    username: str,
    account_status: str,
    local_artifact: str,
    tmp_dir: str,
    restore_job_id: int | None = None,
    progress=None,
) -> dict:
    def report(message: str) -> None:
        if progress is not None:
            progress(message)
        elif restore_job_id is not None:
            _update_restore(restore_job_id, progress_message=message)

    summary = {"initial_password": None, "mailboxes_requiring_password_reset": []}
    report("extracting backup")
    extract_dir = Path(tmp_dir) / "extracted"
    extract_dir.mkdir()
    _safe_extract_tar(local_artifact, extract_dir)

    inner_dirs = [d for d in extract_dir.iterdir() if d.is_dir()]
    if len(inner_dirs) != 1:
        raise BackupError("unexpected full backup artifact layout")
    content_dir = inner_dirs[0]

    manifest_path = content_dir / "manifest.json"
    if not manifest_path.exists():
        raise BackupError("backup artifact has no manifest.json")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("username") != username:
        raise BackupError("backup manifest belongs to another account")
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        names = [entry['domain'] for entry in manifest.get('domains', [])]
        names += list(manifest.get('mail_domains', []))
        names += [entry['domain'] for entry in manifest.get('mail_users', [])]
        if manifest.get('dns_zone'):
            names.append(manifest['dns_zone']['zone'])
        for name in names:
            validate_domain(name)
            for model, field in ((Domain, Domain.domain), (MailDomain, MailDomain.domain), (DnsZone, DnsZone.zone)):
                row = session.scalar(select(model).where(field == name))
                if row is not None and row.account_id != account.id:
                    raise BackupError("backup manifest references another account's domain")

    from daemon import handlers_account, handlers_cron, handlers_database, handlers_dns, handlers_domain, handlers_mail
    from daemon.dns_zone_lookup import label_within_zone

    if account_status in ("terminated", "error"):
        # reactivate_account, not create_account: terminating an account
        # never deletes its historical DB row (see terminate_account), so
        # create_account's own "already exists" guard would always fire
        # here -- found live when that got silently treated as a harmless
        # no-op, the Linux user was never actually recreated, and the
        # restore failed several steps later trying to chown files to a
        # uid that no longer existed.
        report("recreating account")
        recreated = handlers_account.reactivate_account(
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
        summary["initial_password"] = recreated.get("initial_password")

        report("recreating domains")
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

        report("recreating databases")
        for db in manifest.get("databases", []):
            try:
                suffix = db["db_name"][len(username) + 1 :] if db["db_name"].startswith(f"{username}_") else db["db_name"]
                handlers_database.create_database({"username": username, "name": suffix})
            except RuntimeError:
                pass

        report("recreating mail")
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
            summary["mailboxes_requiring_password_reset"] = reset_mailboxes
            report(
                f"recreated {len(reset_mailboxes)} mailbox(es) with new random passwords "
                f"(original passwords are never stored in backups) -- reset via mail.change_password"
            )

        report("recreating cron jobs")
        for cj in manifest.get("cron_jobs", []):
            try:
                handlers_cron.add_cron_job(
                    {"username": username, "schedule": cj["schedule"], "command": cj["command"], "label": cj.get("label", "")}
                )
            except Exception:  # noqa: BLE001
                pass

        dns_zone = manifest.get("dns_zone")
        if dns_zone:
            report("recreating DNS zone")
            try:
                handlers_dns.create_zone({"username": username, "domain": dns_zone["zone"]})
            except RuntimeError:
                pass
            for rec in dns_zone.get("records", []):
                try:
                    # Manifest records carry the FULL record name (that's
                    # what list_records returns); set_record wants the label
                    # relative to the zone. Passing the FQDN straight
                    # through created "www.example.com.example.com" records
                    # -- pre-existing bug fixed as part of the Cloudflare
                    # work (docs/PLAN-cloudflare.md SS2).
                    handlers_dns.set_record(
                        {
                            "domain": dns_zone["zone"],
                            "subdomain": label_within_zone(rec.get("name") or dns_zone["zone"], dns_zone["zone"]),
                            "type": rec["type"],
                            "values": rec["values"],
                            "ttl": rec.get("ttl", 3600),
                            "proxied": rec.get("proxied", False),
                        }
                    )
                except Exception:  # noqa: BLE001
                    pass

    report("restoring files")
    home_tar = content_dir / "home.tar.gz"
    if home_tar.exists():
        pw = _pwnam(username)
        _restore_tree(home_tar, Path(settings.home_base) / username, pw, prefix=username)

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
        report("restoring docroot permissions")
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

    report("restoring databases")
    db_dir = content_dir / "databases"
    if db_dir.exists():
        for dump in db_dir.glob("*.sql.gz"):
            db_name = dump.name[: -len(".sql.gz")]
            with write_session() as session:
                account = session.scalar(select(Account).where(Account.username == username))
                _authorize_item(session, account, "database", db_name)
            _restore_database_dump(db_name, dump)

    report("restoring mail")
    mail_dir = content_dir / "mail"
    if mail_dir.exists():
        for tarball in mail_dir.glob("*.tar.gz"):
            domain = tarball.name[: -len(".tar.gz")]
            validate_domain(domain)
            with write_session() as session:
                account = session.scalar(select(Account).where(Account.username == username))
                if session.scalar(select(MailDomain.id).where(MailDomain.account_id == account.id, MailDomain.domain == domain)) is None:
                    raise BackupError("mail domain is not owned by this account")
            _restore_tree(tarball, Path(settings.mail_base) / domain, _pwnam('vmail'), prefix=domain)
    return summary


def _restore_file(username: str, item_ref: str, local_artifact: str, restore_job_id: int) -> None:
    _update_restore(restore_job_id, progress_message=f"restoring {item_ref}")
    home_dir = Path(settings.home_base) / username
    if not home_dir.exists():
        raise BackupError(f"account '{username}' has no home directory to restore into")
    pw = _pwnam(username)
    _restore_tree(local_artifact, home_dir, pw)


def _restore_tree(artifact, destination, owner, prefix=None):
    """Validate a private archive copy, then extract with destination privileges."""
    if owner.pw_uid <= 0 or owner.pw_gid <= 0:
        raise BackupError("refusing restore as root")
    with tempfile.TemporaryDirectory(prefix="boron-tree-restore-") as scratch:
        private = Path(scratch) / "archive"
        fd = os.open(artifact, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise BackupError("restore archive is not a regular file")
            total = 0
            with os.fdopen(fd, "rb", closefd=False) as source, private.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > settings.cpanel_import_max_upload_bytes:
                        raise BackupError("restore archive exceeds the compressed-size limit")
                    output.write(chunk)
        finally:
            os.close(fd)
        expanded = 0
        with tarfile.open(private) as archive:
            for count, member in enumerate(archive, 1):
                parts = Path(member.name).parts
                if not parts and member.isdir() and prefix is None:
                    continue
                if (count > MAX_BACKUP_ARCHIVE_MEMBERS or not parts or Path(member.name).is_absolute()
                        or '..' in parts or len(parts) > MAX_BACKUP_ARCHIVE_PATH_DEPTH
                        or len(member.name.encode()) > MAX_BACKUP_ARCHIVE_PATH_BYTES
                        or not (member.isreg() or member.isdir() or member.issym() or member.islnk())):
                    raise BackupError("unsupported or unsafe restore archive member")
                if prefix is not None and parts[0] != prefix:
                    raise BackupError("restore archive contains another account or mail domain")
                checked = member.replace(name=str(Path(*parts[1:])) if prefix else member.name)
                if prefix and member.islnk():
                    link_parts = Path(member.linkname).parts
                    if not link_parts or link_parts[0] != prefix:
                        raise BackupError("restore hard link references another account")
                    checked = checked.replace(linkname=str(Path(*link_parts[1:])))
                try:
                    tarfile.data_filter(checked, str(destination))
                except (tarfile.FilterError, OSError) as exc:
                    raise BackupError("unsafe restore archive path or link") from exc
                expanded += member.size if member.isreg() else 0
                if expanded > settings.cpanel_import_max_extracted_bytes:
                    raise BackupError("restore archive exceeds the expansion limit")
        with private.open('rb') as stream:
            compressed = stream.read(2) == b'\x1f\x8b'
        argv = ['tar', '-xz' if compressed else '-x', '-f', '-', '--no-same-owner', '--no-same-permissions', '-C', str(destination)]
        if prefix is not None:
            argv.append('--strip-components=1')
        result = run(argv, input_path=str(private), uid=owner.pw_uid, gid=owner.pw_gid, timeout=600)
        if not result.ok:
            raise BackupError("account-privileged archive extraction failed")


def _restore_database(item_ref: str, local_artifact: str, restore_job_id: int) -> None:
    _update_restore(restore_job_id, progress_message=f"restoring database {item_ref}")
    _restore_database_dump(item_ref, Path(local_artifact))


def _restore_databases(username: str, local_artifact: str, tmp_dir: str, restore_job_id: int) -> None:
    account_id = _resolve_account_id(username)
    with write_session() as session:
        owned = set(session.scalars(select(DatabaseGrant.db_name).where(DatabaseGrant.account_id == account_id)).all())
    extract_dir = Path(tmp_dir) / "database-set"
    extract_dir.mkdir()
    _safe_extract_tar(local_artifact, extract_dir)
    dumps = sorted((extract_dir / "databases").glob("*.sql.gz"))
    if not dumps:
        raise BackupError("database archive contains no database dumps")
    for index, dump in enumerate(dumps, start=1):
        name = dump.name[:-len('.sql.gz')]
        if name not in owned:
            raise BackupError(f"database '{name}' is not owned by this account")
        _update_restore(restore_job_id, progress_message=f"restoring database {index} of {len(dumps)}: {name}")
        _restore_database_dump(name, dump)


def _restore_mailbox(item_ref: str, local_artifact: str, restore_job_id: int) -> None:
    if "@" not in item_ref:
        raise BackupError("item_ref for a mailbox restore must be 'local_part@domain'")
    local_part, domain = item_ref.split("@", 1)
    _update_restore(restore_job_id, progress_message=f"restoring mailbox {item_ref}")
    mail_dir = Path(settings.mail_base) / domain
    if not mail_dir.exists():
        raise BackupError(f"mail domain '{domain}' does not exist -- create it before restoring a mailbox into it")
    _restore_tree(local_artifact, mail_dir / local_part, _pwnam('vmail'), prefix=local_part)


def _run_restore_job(restore_job_id: int) -> None:
    tmp_dir = None
    try:
        with write_session() as session:
            restore_job = session.get(RestoreJob, restore_job_id)
            if restore_job is None or restore_job.status != "pending":
                return
            backup_job = session.get(BackupJob, restore_job.backup_job_id)
            destination = session.get(BackupDestination, backup_job.destination_id)
            account = session.get(Account, restore_job.account_id)
            if account is None:
                raise BackupError("restore account no longer exists")
            if account.status != "active":
                raise BackupError(f"cannot run restore for an account in status '{account.status}'")
            username = account.username
            account_status = account.status
            kind = restore_job.kind
            item_ref = restore_job.item_ref
            _authorize_item(session, account, kind, item_ref)
            artifact_path = backup_job.artifact_path
            dest_kind = destination.kind
            restore_job.status = "running"
            restore_job.progress_message = "fetching backup artifact"

        tmp_dir = tempfile.mkdtemp(dir=settings.backup_staging_dir, prefix=f"restore-{restore_job_id}-")
        local_artifact = _fetch_artifact_locally(artifact_path, dest_kind, tmp_dir)

        if kind == "full":
            _restore_full(username, account_status, local_artifact, tmp_dir, restore_job_id)
        elif kind == "file":
            _restore_file(username, item_ref, local_artifact, restore_job_id)
        elif kind == "database":
            _restore_database(item_ref, local_artifact, restore_job_id)
        elif kind == "databases":
            _restore_databases(username, local_artifact, tmp_dir, restore_job_id)
        elif kind == "mailbox":
            _restore_mailbox(item_ref, local_artifact, restore_job_id)
        else:
            raise BackupError(f"unknown restore kind '{kind}'")

        _update_restore(restore_job_id, status="completed", progress_message="completed", completed_at=utcnow())
    except Exception as exc:  # noqa: BLE001
        logger.exception("restore job %d failed", restore_job_id)
        _update_restore(restore_job_id, status="failed", error=str(exc), progress_message="failed", completed_at=utcnow())
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
