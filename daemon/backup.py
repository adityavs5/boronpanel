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
import re
import contextlib
import stat
import subprocess
import secrets
import shutil
import tarfile
import tempfile
from shared.request_context import ContextThreadPoolExecutor as ThreadPoolExecutor
from contextlib import ExitStack
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
    DatabaseUser,
    DatabaseUserGrant,
    DkimKey,
    Domain,
    DnsZone,
    MailDomain,
    MailUser,
    RestoreJob,
    SnapshotRun,
    SnapshotRestore,
    utcnow,
)
from shared.validation import NotFoundError, ValidationError, validate_domain, validate_username, validate_mailbox_local_part

from daemon import audit, cron, dnsprovider, events, mariadb, rclone
from daemon.archive_limits import ArchiveMetadataTooLarge, open_guarded_tar
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


class BackupError(ValidationError):
    pass


class BackupNotFoundError(BackupError, NotFoundError):
    """The account, backup or restore job does not exist (HTTP 404)."""


# An absolute host path inside an error message (staging files, homes).
_HOST_PATH_RE = re.compile(r"(?<![\w.:/])/(?:[\w.@+-]+/)*[\w.@+-]+/?")


def _job_error(exc: BaseException, label: str) -> str:
    """The text a backup or restore job row may show its account.

    Job rows are read back by the customer panel. A validation failure keeps
    its explanation, with any absolute host path replaced; anything else is
    reported only by a reference. The full exception is always logged under
    that reference.
    """
    reference = secrets.token_hex(8)
    logger.error("%s failed (reference: %s)", label, reference, exc_info=exc)
    if isinstance(exc, ValidationError):
        return _HOST_PATH_RE.sub("[server path]", str(exc))[:1000]
    return f"The operation failed because of an internal error (reference: {reference})"


def _tool_failure(message: str, result) -> BackupError:
    """Log a failed archiving command's output; return the short error.

    tar's stderr names host paths and staging files, so it stays in the log.
    """
    reference = secrets.token_hex(8)
    logger.error("%s (reference: %s): %s", message, reference, (result.stderr or "").strip()[-2000:])
    return BackupError(f"{message} (reference: {reference})")


def _safe_extract_tar(archive_path: str | Path, destination: str | Path) -> None:
    """Extract a backup component with traversal and expansion limits."""
    try:
        with open_guarded_tar(archive_path) as archive:
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
    except (tarfile.TarError, ArchiveMetadataTooLarge) as exc:
        # The filter's message names the extraction directory on this host.
        logger.warning("refused archive component %s: %s", archive_path, exc)
        raise BackupError("The backup contains an unsafe or unreadable archive component") from exc


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
        from daemon.snapshot_storage import validate_local_destination
        local_path = str(validate_local_destination(local_path))
        Path(local_path).mkdir(mode=0o700, parents=True, exist_ok=True)
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


def delete_schedule(params: dict) -> dict:
    """Remove a legacy schedule (server default or per-account override).

    Its BackupJob history is unaffected; only future scheduled runs stop.
    """
    schedule_id = int(params["id"])
    with write_session() as session:
        schedule = session.get(BackupSchedule, schedule_id)
        if schedule is None:
            raise NotFoundError(f"backup schedule {schedule_id} not found")
        result = _schedule_to_dict(schedule)
        session.delete(schedule)
    return {**result, "status": "deleted"}


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
    with audit.step("backup: validate and queue job"):
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
    trigger_source = "customer" if params.get("customer_scope") else params.get("trigger", "manual")

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise ValidationError(f"account '{username}' not found")

        if account.status != "active":
            raise ValidationError("Only active accounts can start a new backup")

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
        schedule = get_effective_schedule(session, account.id)
        if destination_id is None:
            if schedule is None:
                # Central snapshot destinations are a different model. Do not
                # reuse their IDs or silently create a recurring schedule.
                path = Path(settings.backup_dir) / "account-archives"
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
                if path.is_symlink() or path.stat().st_uid != os.geteuid():
                    raise ValidationError("The local backup directory needs administrator permission repair")
                path.chmod(0o700)
                if shutil.disk_usage(path).free < 512 * 1024 * 1024:
                    raise ValidationError("Less than 512 MB is free on backup storage. Free space before starting a backup.")
                destination = session.scalar(select(BackupDestination).where(
                    BackupDestination.kind == "local", BackupDestination.local_path == str(path)))
                if destination is None:
                    name = "On-demand account archives"
                    if session.scalar(select(BackupDestination.id).where(BackupDestination.name == name)):
                        name += " " + secrets.token_hex(4)
                    destination = BackupDestination(name=name, kind="local", local_path=str(path))
                    session.add(destination)
                    session.flush()
                destination_id = destination.id
            else:
                destination_id = schedule.destination_id
        else:
            destination_id = int(destination_id)
            if params.get("customer_scope") and (schedule is None or destination_id != schedule.destination_id):
                raise BackupError("Choose the backup destination configured for this account")
        selected_destination = session.get(BackupDestination, destination_id)
        if selected_destination is None:
            raise BackupError(f"destination {destination_id} not found")
        if selected_destination.kind == "local":
            from daemon.snapshot_storage import validate_local_destination
            local_path = validate_local_destination(selected_destination.local_path)
            if not local_path.is_absolute() or local_path.is_symlink():
                raise ValidationError("The local backup destination needs administrator permission repair")
            local_path.mkdir(mode=0o700, parents=True, exist_ok=True)
            if local_path.stat().st_uid != os.geteuid():
                raise ValidationError("The local backup destination needs administrator permission repair")
            if shutil.disk_usage(local_path).free < 512 * 1024 * 1024:
                raise ValidationError("Less than 512 MB is free on backup storage. Free space before starting a backup.")

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
        audit.change("backup_job", None, {
            "id": job_id, "kind": kind, "destination_id": destination_id,
            "status": "pending",
        })

    _executor.submit(_run_backup_job, job_id)
    return result


def _resolve_account_id(username: str) -> int:
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise BackupNotFoundError(f"account '{username}' not found")
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
            raise BackupNotFoundError(f"backup job {job_id} not found")
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
            errors.seek(0)
            logger.error("file backup tar for '%s' exited %d: %s", username, result.returncode,
                         errors.read()[-2000:].decode("utf-8", "replace").strip())
            raise BackupError("account-privileged file backup failed")


def _as_aware_utc(value):
    import datetime as dt

    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value


def _dump_database(db_name: str, output_path: Path) -> None:
    from daemon.snapshot_databases import dump_database
    plain = output_path.with_suffix(output_path.suffix + ".sql")
    try:
        dump_database(db_name, plain, output_path.parent)
        with plain.open("rb") as source, gzip.open(output_path, "xb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
    finally:
        plain.unlink(missing_ok=True)


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
        # Creation order: a restore re-adds parent domains before subdomains.
        domains = session.scalars(select(Domain).where(Domain.account_id == account.id).order_by(Domain.id)).all()
        databases = session.scalars(select(DatabaseGrant).where(DatabaseGrant.account_id == account.id)).all()
        database_names = {db.id: db.db_name for db in databases}
        database_users = []
        for user in session.scalars(select(DatabaseUser).where(DatabaseUser.account_id == account.id)
                                    .order_by(DatabaseUser.id)).all():
            links = session.scalars(select(DatabaseUserGrant).where(DatabaseUserGrant.database_user_id == user.id)).all()
            database_users.append({
                "db_user": user.db_user, "host": user.host,
                "grants": [{"db_name": database_names[link.database_grant_id], "preset": link.privileges,
                            "privileges": link.privilege_list.split(",") if link.privilege_list else []}
                           for link in links if link.database_grant_id in database_names],
            })
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
            # Logins and their database access (passwords are never stored).
            # Optional for readers: archives made before this have none.
            "database_users": database_users,
            "mail_domains": [m.domain for m in mail_domains],
            "mail_users": [{"local_part": u.local_part, "domain": u.domain, "quota_mb": u.quota_mb} for u in mail_users],
            "cron_jobs": [],
            "dns_zone": None,
        }
        zone_name = dns_zone_row.zone if dns_zone_row is not None else None

    _update_job(job_id, progress_message="backing up cron jobs")
    try:
        manifest["cron_jobs"] = cron.list_jobs(username)
    except (cron.CronError, cron.CrontabCommandError):
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
        db_dir.mkdir(mode=0o700)
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
                    raise _tool_failure(f"Could not archive the mail of {domain}", result)

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
        raise _tool_failure("Could not assemble the backup archive", result)
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
    content.mkdir(parents=True, exist_ok=True, mode=0o700)
    content.chmod(0o700)
    for index, name in enumerate(names, start=1):
        _update_job(job_id, progress_message=f"dumping database {index} of {len(names)}: {name}")
        _dump_database(name, content / f"{name}.sql.gz")
    (staging_dir / "manifest.json").write_text(json.dumps({"kind": "databases", "databases": names}, indent=2) + "\n")
    artifact = staging_dir.parent / f"{staging_dir.name}-databases.tar"
    result = run(["tar", "cf", str(artifact), "-C", str(staging_dir), "manifest.json", "databases"], timeout=1800)
    if not result.ok:
        raise _tool_failure("Could not assemble the database backup archive", result)
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
        raise _tool_failure(f"Could not archive the mailbox {item_ref}", result)
    return artifact


def _private_staging_directory(job_id: int) -> Path:
    """Create the legacy archive worker's credential-safe scratch directory.

    Database exports write a short-lived MariaDB option file beside the dump.
    The snapshot exporter has always used a 0700 service-owned directory for
    that purpose, but the legacy/on-demand archive path inherited the daemon's
    ordinary umask and therefore created ``job-*`` as 0755 on a fresh install.
    Keep the same ownership and symlink boundary on both backup engines.
    """
    base = Path(settings.backup_staging_dir)
    if not base.is_absolute() or ".." in base.parts or base == Path("/"):
        raise ValidationError("Invalid backup staging directory")
    for path in [*reversed(base.parents), base]:
        if path.is_symlink():
            raise ValidationError("Backup staging directory cannot use symbolic links")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    if base.stat().st_uid != os.geteuid():
        raise ValidationError("Backup staging directory has an unexpected owner")
    base.chmod(0o700)
    target = base / f"job-{int(job_id)}"
    if target.is_symlink():
        raise ValidationError("Backup staging directory cannot use symbolic links")
    target.mkdir(exist_ok=True, mode=0o700)
    if not target.is_dir() or target.stat().st_uid != os.geteuid():
        raise ValidationError("Backup staging directory has an unexpected owner")
    target.chmod(0o700)
    return target


def _run_backup_job(job_id: int) -> None:
    staging_dir = Path(settings.backup_staging_dir) / f"job-{job_id}"
    kind = None
    account_snapshot = None
    guard = ExitStack()
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

        from daemon.snapshot_jobs import lock
        guard.enter_context(lock(f'account-{account_snapshot.id}'))
        with write_session() as session:
            current = session.get(Account, account_snapshot.id)
            if current is None or current.username != username or current.status != 'active':
                raise BackupError('Backup account changed while waiting to start')
            _authorize_item(session, current, kind, item_ref)
        staging_dir = _private_staging_directory(job_id)
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
            final_path = _publish_local_artifact(dest_local_path, artifact_name, local_artifact)
        else:
            final_path = (
                f"{dest_remote}:{dest_prefix}/{artifact_name}" if dest_prefix else f"{dest_remote}:{artifact_name}"
            )
            rclone.copy(str(local_artifact), final_path)

        shutil.rmtree(staging_dir, ignore_errors=True)
        guard.close()
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
        error = _job_error(exc, f"backup job {job_id}")
        shutil.rmtree(staging_dir, ignore_errors=True)
        guard.close()
        _update_job(job_id, status="failed", error=error, progress_message="failed", completed_at=utcnow())
        if kind == "full" and account_snapshot is not None:
            events.emit("backup.failed", account_snapshot, job_id=job_id, error=error)
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        guard.close()


def _enforce_retention(job_id: int) -> None:
    from daemon.snapshot_jobs import lock
    # Restore queue admission and artifact expiry must be mutually exclusive.
    with lock('queue'):
        _enforce_retention_locked(job_id)


def _enforce_retention_locked(job_id: int) -> None:
    with write_session() as session:
        job = session.get(BackupJob, job_id)
        account_id = job.account_id
        destination_id = job.destination_id
        schedule = get_effective_schedule(session, account_id)
        destination = session.get(BackupDestination, destination_id)
        if schedule is not None:
            retention_count = schedule.retention_count
        elif job.trigger == "customer":
            retention_count = 3
        elif (destination.kind == "local" and destination.local_path ==
              str(Path(settings.backup_dir) / "account-archives")):
            # Bound the new self-service storage; existing unscheduled
            # destinations retain their operator-defined behavior.
            retention_count = 3
        else:
            return
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
        with write_session() as session:
            if session.scalar(select(RestoreJob.id).where(RestoreJob.backup_job_id == old_job_id,
                    RestoreJob.status.in_(['pending', 'running']))) is not None:
                continue
        try:
            if dest_kind == "local":
                if artifact_path:
                    path = Path(artifact_path)
                    if path.parent != Path(destination.local_path):
                        raise BackupError("Backup artifact is outside its destination")
                    with _local_directory(path.parent) as fd:
                        os.unlink(path.name, dir_fd=fd)
            elif artifact_path:
                rclone.delete_path(artifact_path)
        except Exception:  # noqa: BLE001
            logger.exception("failed to delete old backup artifact for retention (job %d)", old_job_id)
            continue
        with write_session() as session:
            old_job = session.get(BackupJob, old_job_id)
            if old_job is not None:
                if session.scalar(select(RestoreJob.id).where(RestoreJob.backup_job_id == old_job_id)) is not None:
                    # Keep the referenced history row; its artifact has expired.
                    old_job.status = 'expired'
                    old_job.artifact_path = None
                    old_job.progress_message = 'Expired by backup retention'
                else:
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
        path = Path(artifact_path)
        # Copy a pinned regular file into private staging. Never hand callers
        # a mutable pathname that will be reopened later as root.
        with _local_directory(path.parent) as directory:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
                    raise BackupError("Backup artifact is not a protected regular file")
                target = Path(tmp_dir) / path.name
                with target.open("xb") as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                target.chmod(0o600)
                return str(target)
    local_copy = str(Path(tmp_dir) / Path(artifact_path.split(":", 1)[1]).name)
    rclone.copy_from_remote(artifact_path, local_copy)
    return local_copy


def browse_backup(params: dict) -> dict:
    job_id = int(params["job_id"])
    account_id = _resolve_account_id(validate_username(params["username"]))
    with write_session() as session:
        job = session.get(BackupJob, job_id)
        if job is None or job.account_id != account_id:
            raise BackupNotFoundError(f"backup job {job_id} not found")
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
    with audit.step("restore: validate and queue job"):
        with lock('queue'):
            return _trigger_restore_locked(params)


def _require_restorable(status: str, kind: str, backup_kind: str) -> None:
    """A restore runs on an active account, or recreates a terminated one.

    Termination released the account's domains, databases and mail, so a
    terminated (or partly terminated, 'error') account can only come back
    through a full restore of a full backup, which recreates all of it.
    """
    if status == "active":
        return
    if status in ("terminated", "error"):
        if kind == "full" and backup_kind == "full":
            return
        raise BackupError("This account is terminated. Restore a full backup to recreate it first.")
    raise BackupError(f"cannot run restore for an account in status '{status}'")


def _trigger_restore_locked(params: dict) -> dict:
    backup_job_id = int(params["backup_job_id"])
    item_ref = params.get("item_ref")
    account_id = _resolve_account_id(validate_username(params["username"]))

    with write_session() as session:
        backup_job = session.get(BackupJob, backup_job_id)
        if backup_job is None or backup_job.account_id != account_id:
            raise BackupNotFoundError(f"backup job {backup_job_id} not found")
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
        _require_restorable(account.status, effective_kind, backup_job.kind)
        if account.status != "active" and params.get("customer_scope"):
            # Recreating a terminated account brings back a whole account an
            # administrator ended; a reseller would also pass its plan's
            # account and disk limits (terminated accounts do not count).
            raise BackupError("Only an administrator can restore a terminated account.")
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
        audit.change("restore_job", None, {
            "id": restore_job_id, "backup_job_id": backup_job_id,
            "kind": effective_kind, "status": "pending",
        })

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
            raise BackupNotFoundError(f"restore job {restore_job_id} not found")
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


def _full_artifact_content(username: str, local_artifact: str, tmp_dir: str) -> tuple[Path, dict]:
    """Extract a full backup's outer tar; return its content dir and manifest.

    The outer archive holds one `job-N/` directory with manifest.json,
    home.tar.gz, databases/*.sql.gz and mail/<domain>.tar.gz. A full restore
    and a single-item restore from a full backup both start here.
    """
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
    try:
        manifest = json.loads(manifest_path.read_text())
    except ValueError:
        raise BackupError("backup artifact has an unreadable manifest.json") from None
    if not isinstance(manifest, dict) or manifest.get("username") != username:
        raise BackupError("backup manifest belongs to another account")
    return content_dir, manifest


def _normalized_item_path(item_ref: str) -> str:
    path = Path(item_ref)
    parts = [part for part in path.parts if part not in ("", ".")]
    if not parts or path.is_absolute() or ".." in parts:
        raise BackupError("file path must stay inside the account home")
    return Path(*parts).as_posix()


def _restore_item_from_full(
    username: str, kind: str, item_ref: str | None, local_artifact: str, tmp_dir: str, restore_job_id: int,
) -> None:
    """Restore exactly one item (a file or directory, one database, every
    database, or one mailbox) from a full backup artifact.

    The single-item restorers expect that item's own artifact. Handing them
    the outer full .tar extracted the whole job directory into the home
    (file) or failed (database, databases, mailbox).
    """
    _update_restore(restore_job_id, progress_message="extracting backup")
    content_dir, _manifest = _full_artifact_content(username, local_artifact, tmp_dir)
    if kind == "file":
        relative = _normalized_item_path(item_ref)
        home_tar = content_dir / "home.tar.gz"
        if not home_tar.is_file():
            raise BackupError("this backup contains no home directory files")
        home_dir = Path(settings.home_base) / username
        if not home_dir.exists():
            raise BackupError(f"account '{username}' has no home directory to restore into")
        _update_restore(restore_job_id, progress_message=f"restoring {relative}")
        _restore_tree(home_tar, home_dir, _pwnam(username), prefix=username, select=relative)
    elif kind == "database":
        dump = content_dir / "databases" / f"{item_ref}.sql.gz"
        if "/" in item_ref or not dump.is_file():
            raise BackupError(f"database '{item_ref}' is not in this backup")
        _update_restore(restore_job_id, progress_message=f"restoring database {item_ref}")
        _restore_database_dump(item_ref, dump)
    elif kind == "databases":
        _restore_database_dumps(username, content_dir / "databases", restore_job_id)
    elif kind == "mailbox":
        local_part, _separator, domain = item_ref.partition("@")
        validate_mailbox_local_part(local_part)
        validate_domain(domain)
        tarball = content_dir / "mail" / f"{domain}.tar.gz"
        if not tarball.is_file():
            raise BackupError(f"mailbox '{item_ref}' is not in this backup")
        mail_dir = Path(settings.mail_base) / domain
        if not mail_dir.exists():
            raise BackupError(f"mail domain '{domain}' does not exist -- create it before restoring a mailbox into it")
        _update_restore(restore_job_id, progress_message=f"restoring mailbox {item_ref}")
        # Members are `<domain>/<local_part>/...`: strip the domain and
        # extract only this mailbox.
        _restore_tree(tarball, mail_dir, _pwnam('vmail'), prefix=domain, select=local_part)
    else:
        raise BackupError(f"unknown restore kind '{kind}'")


def _reactivate_for_restore(username: str, params: dict) -> dict:
    """Recreate a terminated account's Linux identity for a restore/import.

    Termination released the account's uid, so the uid still stored on the
    terminated row is stale (and may already belong to another tenant).
    Left in place, the new identity never matched it and cgroup setup
    refused the restore. Clear it first so the new uid becomes the account's
    identity; then remap anything in a surviving home that the stale uid
    owns. If recreation fails, a home this call created is removed, so no
    directory is left owned by an unassigned uid that useradd will recycle.
    """
    from daemon import handlers_account, sysops

    home = Path(settings.home_base) / username
    home_existed = home.exists()
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        stale_uid, stale_gid = account.uid, account.gid
        account.uid = None
        account.gid = None
    try:
        recreated = handlers_account.reactivate_account(params)
    except Exception:
        # Keep the released identity on record: a retry against a surviving
        # home still needs it to remap the files that identity owns.
        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == username))
            if account is not None and account.status == "terminated" and account.uid is None:
                account.uid, account.gid = stale_uid, stale_gid
        if not home_existed and not sysops.user_exists(username):
            try:
                # The user is already gone, so this only removes the
                # orphaned home, without following links or crossing mounts.
                sysops.delete_linux_user(username, remove_home=True)
            except Exception:  # noqa: BLE001
                logger.exception("could not remove the orphaned home of '%s' after a failed restore", username)
        raise
    if home_existed:
        pw = _pwnam(username)
        remaps = []
        if stale_uid is not None and stale_uid != pw.pw_uid:
            remaps.append((f"--from={stale_uid}", str(pw.pw_uid)))
        if stale_gid is not None and stale_gid != pw.pw_gid:
            remaps.append((f"--from=:{stale_gid}", f":{pw.pw_gid}"))
        for selector, owner in remaps:
            # -R without -L/-H never follows links; --from limits the change
            # to entries the released identity still owns.
            result = run(["chown", "-R", "--no-dereference", selector, owner, "--", str(home)], timeout=1800)
            if not result.ok:
                raise BackupError("could not reassign the restored home directory to the recreated account")
    return recreated


def _note_not_restored(summary: dict, item: str, exc: BaseException) -> None:
    """Record an item a full restore could not bring back, and why.

    The summary is shown to the account: a validation message is kept as
    the reason, anything else is logged and named only by a reference.
    """
    if isinstance(exc, ValidationError):
        reason = _HOST_PATH_RE.sub("[server path]", str(exc))[:300]
    else:
        reference = secrets.token_hex(8)
        logger.error("full restore could not recreate %s (reference: %s)", item, reference, exc_info=exc)
        reason = f"internal error (reference: {reference})"
    summary["not_restored"].append({"item": item, "reason": reason})


def _noted(summary: dict, item: str) -> bool:
    return any(entry["item"] == item for entry in summary["not_restored"])


def _foreign_names(username: str, manifest: dict) -> set[str]:
    """Hostnames in the manifest that another account holds now.

    Termination releases an account's hostnames, so another tenant may have
    added one before the backup is restored. The restore leaves such a name
    (its site, mail and DNS zone) alone and reports it, instead of refusing
    the whole restore or taking the name over.
    """
    names = {entry["domain"] for entry in manifest.get("domains", [])}
    names.update(manifest.get("mail_domains", []))
    names.update(entry["domain"] for entry in manifest.get("mail_users", []))
    if manifest.get("dns_zone"):
        names.add(manifest["dns_zone"]["zone"])
    foreign = set()
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        for name in names:
            validate_domain(name)
            for model, field in ((Domain, Domain.domain), (MailDomain, MailDomain.domain), (DnsZone, DnsZone.zone)):
                row = session.scalar(select(model).where(field == name))
                if row is not None and row.account_id != account.id:
                    foreign.add(name)
    return foreign


def _domain_params(username: str, entry: dict) -> dict:
    """add_domain parameters that recreate a backed-up domain as it was."""
    params = {"username": username, "domain": entry["domain"], "kind": entry.get("kind") or "addon"}
    docroot = entry.get("docroot")
    if params["kind"] == "subdomain" and docroot:
        try:
            relative = Path(docroot).relative_to(Path(settings.home_base) / username)
        except ValueError:
            relative = None
        if relative is not None and relative.parts and relative != Path(entry["domain"]) / "public_html":
            # Created with its own folder or its parent's document root.
            params["document_root_mode"] = "custom"
            params["document_root"] = relative.as_posix()
    return params


def _parked_target(manifest: dict, entry: dict) -> str | None:
    """The domain a backed-up parked domain points at. The manifest stores
    no target, only the shared document root; the primary domain wins."""
    candidates = [other for other in manifest.get("domains", [])
                  if other.get("kind") != "parked" and entry.get("docroot")
                  and other.get("docroot") == entry.get("docroot")]
    candidates.sort(key=lambda other: other.get("kind") != "primary")
    return candidates[0]["domain"] if candidates else None


def _recreate_domains(username: str, manifest: dict, foreign: set[str], summary: dict) -> list[str]:
    from daemon import handlers_domain, parked

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        present = set(session.scalars(select(Domain.domain).where(Domain.account_id == account.id)).all())
    recreated = []
    entries = manifest.get("domains", [])
    # add_domain refuses kind 'parked': parked domains are recreated through
    # parked.add_parked_domain, after the domains they point at.
    ordered = ([entry for entry in entries if entry.get("kind") != "parked"]
               + [entry for entry in entries if entry.get("kind") == "parked"])
    for entry in ordered:
        name = entry["domain"]
        if name in foreign or name in present:
            continue
        try:
            if entry.get("kind") == "parked":
                params = {"username": username, "parked_domain": name}
                target = _parked_target(manifest, entry)
                if target:
                    params["target_domain"] = target
                parked.add_parked_domain(params)
            else:
                handlers_domain.add_domain(_domain_params(username, entry))
        except Exception as exc:  # noqa: BLE001 - reported; the rest of the account still restores
            _note_not_restored(summary, f"domain {name}", exc)
            continue
        present.add(name)
        recreated.append(name)
    return recreated


def _recreate_databases(username: str, manifest: dict, summary: dict) -> set[str]:
    """Create the backed-up databases the account no longer has.

    Each comes back with its original application login. Database passwords
    are never stored in a backup, so that login gets a new random password,
    reported in the summary.
    """
    from daemon import handlers_database

    prefix = f"{username}_"
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        present = set(session.scalars(select(DatabaseGrant.db_name).where(DatabaseGrant.account_id == account.id)).all())
    recreated = set()
    for entry in manifest.get("databases", []):
        db_name = entry["db_name"]
        if db_name in present:
            continue
        if not db_name.startswith(prefix) or db_name == prefix:
            # Renamed databases keep names db.create cannot produce.
            summary["not_restored"].append({
                "item": f"database {db_name}",
                "reason": f"only databases named {prefix}<name> can be recreated automatically",
            })
            continue
        try:
            created = handlers_database.create_database({"username": username, "name": db_name[len(prefix):]})
        except Exception as exc:  # noqa: BLE001
            _note_not_restored(summary, f"database {db_name}", exc)
            continue
        recreated.add(db_name)
        summary["database_users_requiring_password_reset"].append(created.get("db_user") or entry.get("db_user") or db_name)
    return recreated


def _recreate_database_users(username: str, manifest: dict, recreated_databases: set[str], summary: dict) -> None:
    """Recreate the additional database logins the account no longer has.

    Their database access is granted again for every login this restore
    created and for every database it recreated. Access the account changed
    itself between two existing objects is left as it is now.
    """
    from daemon import handlers_database

    prefix = f"{username}_"
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        present = {(row[0], row[1]) for row in session.execute(
            select(DatabaseUser.db_user, DatabaseUser.host).where(DatabaseUser.account_id == account.id))}
        databases = set(session.scalars(select(DatabaseGrant.db_name).where(DatabaseGrant.account_id == account.id)).all())
    for entry in manifest.get("database_users") or []:
        db_user, host = entry.get("db_user"), entry.get("host") or "localhost"
        if not isinstance(db_user, str) or not isinstance(host, str):
            continue
        login = db_user if host == "localhost" else f"{db_user}@{host}"
        created = False
        if (db_user, host) not in present:
            if not db_user.startswith(prefix) or db_user == prefix:
                summary["not_restored"].append({
                    "item": f"database user {login}",
                    "reason": f"only logins named {prefix}<name> can be recreated automatically",
                })
                continue
            try:
                handlers_database.create_user({"username": username, "name": db_user[len(prefix):], "host": host})
            except Exception as exc:  # noqa: BLE001
                _note_not_restored(summary, f"database user {login}", exc)
                continue
            created = True
            present.add((db_user, host))
            summary["database_users_requiring_password_reset"].append(login)
        for grant in entry.get("grants") or []:
            db_name = grant.get("db_name") if isinstance(grant, dict) else None
            if db_name not in databases or not (created or db_name in recreated_databases):
                continue
            try:
                handlers_database.grant_user({
                    "username": username, "user": db_user, "database": db_name, "host": host,
                    "preset": grant.get("preset") or "all", "privileges": grant.get("privileges") or None,
                })
            except Exception as exc:  # noqa: BLE001
                _note_not_restored(summary, f"access of {login} to {db_name}", exc)


def _recreate_mail(username: str, manifest: dict, foreign: set[str], summary: dict) -> None:
    from daemon import handlers_mail

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        domains = set(session.scalars(select(MailDomain.domain).where(MailDomain.account_id == account.id)).all())
        mailboxes = {(row[0], row[1]) for row in session.execute(
            select(MailUser.local_part, MailUser.domain)
            .join(MailDomain, MailDomain.id == MailUser.mail_domain_id)
            .where(MailDomain.account_id == account.id))}
    for domain in manifest.get("mail_domains", []):
        if domain in foreign or domain in domains:
            continue
        try:
            handlers_mail.create_mail_domain({"username": username, "domain": domain})
        except Exception as exc:  # noqa: BLE001
            _note_not_restored(summary, f"mail domain {domain}", exc)
            continue
        domains.add(domain)
    # Mailbox passwords are never stored in a backup (a manifest holding
    # them would be a secret-handling regression), so every recreated
    # mailbox gets a fresh random one and the summary lists them for reset.
    for entry in manifest.get("mail_users", []):
        local_part, domain = entry["local_part"], entry["domain"]
        if domain in foreign or domain not in domains or (local_part, domain) in mailboxes:
            continue
        params = {"username": username, "domain": domain, "local_part": local_part,
                  "password": mariadb.generate_password()}
        if entry.get("quota_mb") is not None:
            params["quota_mb"] = entry["quota_mb"]
        try:
            handlers_mail.create_mailbox(params)
        except Exception as exc:  # noqa: BLE001
            _note_not_restored(summary, f"mailbox {local_part}@{domain}", exc)
            continue
        mailboxes.add((local_part, domain))
        summary["mailboxes_requiring_password_reset"].append(f"{local_part}@{domain}")


def _recreate_cron_jobs(username: str, manifest: dict, summary: dict) -> None:
    """Add the saved cron jobs the crontab no longer has (same schedule and command)."""
    from daemon import handlers_cron

    saved = manifest.get("cron_jobs") or []
    if not saved:
        return
    try:
        present = {(job["schedule"], job["command"]) for job in cron.list_jobs(username)}
    except Exception as exc:  # noqa: BLE001
        _note_not_restored(summary, "cron jobs", exc)
        return
    for entry in saved:
        key = (entry.get("schedule"), entry.get("command"))
        if key in present:
            continue
        try:
            handlers_cron.add_cron_job({"username": username, "schedule": entry["schedule"],
                                        "command": entry["command"], "label": entry.get("label", "")})
        except Exception as exc:  # noqa: BLE001
            _note_not_restored(summary, f"cron job {entry.get('label') or entry.get('schedule')}", exc)
            continue
        present.add(key)


def _record_name(record: dict, zone: str) -> str:
    return str(record.get("name") or zone).rstrip(".").lower()


def _restore_apex_nameservers(zone: str, rrset: dict, records: list[dict], summary: dict) -> None:
    """Restore a custom delegation the way the Nameservers page sets one.

    Both record editors refuse the zone-apex NS rrset: it is managed with
    the glue of in-zone nameservers. The glue comes from the backup's own
    address records; when one was not saved, set_nameservers names the
    nameserver that needs it and the delegation is reported, not guessed.
    """
    from daemon import nameservers

    wanted = [str(value).rstrip(".").lower() for value in rrset.get("values") or []]
    default, _default_glue = nameservers.default_delegation(zone)
    if not wanted or sorted(wanted) == sorted(name.rstrip(".").lower() for name in default):
        return  # create_zone already delegates the zone this way
    glue = {}
    for host in wanted:
        if host != zone and not host.endswith("." + zone):
            continue  # out-of-zone nameservers are resolved by their owner
        for rtype in ("A", "AAAA"):
            match = next((rec for rec in records if rec.get("type") == rtype and rec.get("values")
                          and _record_name(rec, zone) == host), None)
            if match is not None:
                glue[host] = str(match["values"][0])
                break
    try:
        nameservers.set_nameservers({"domain": zone, "nameservers": wanted, "glue": glue})
    except Exception as exc:  # noqa: BLE001
        _note_not_restored(summary, f"nameservers of {zone}", exc)


def _recreate_dns_zone(username: str, dns_zone: dict, restore_records: bool, summary: dict) -> None:
    """Recreate the saved DNS zone if it is missing, then apply its records.

    Records are applied only to a zone this restore recreated (or to every
    zone of a reactivated account). A zone that existed already holds the
    account's current DNS, which a full restore does not roll back.
    """
    from daemon import handlers_dns
    from daemon.dns_zone_lookup import label_within_zone

    zone = dns_zone["zone"]
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        owned = session.scalar(select(DnsZone.id).where(DnsZone.account_id == account.id, DnsZone.zone == zone)) is not None
    if not owned:
        try:
            handlers_dns.create_zone({"username": username, "domain": zone})
        except Exception as exc:  # noqa: BLE001
            _note_not_restored(summary, f"DNS zone {zone}", exc)
            return
        restore_records = True
    if not restore_records:
        return
    records = [rec for rec in dns_zone.get("records", []) if isinstance(rec, dict)]
    apex_ns = next((rec for rec in records if rec.get("type") == "NS" and _record_name(rec, zone) == zone), None)
    # First: set_nameservers writes in-zone glue as single addresses, and the
    # saved A/AAAA record sets applied below then replace them whole.
    if apex_ns is not None:
        _restore_apex_nameservers(zone, apex_ns, records, summary)
    with write_session() as session:
        keys = session.scalars(select(DkimKey).where(
            (DkimKey.domain == zone) | DkimKey.domain.endswith("." + zone, autoescape=True))).all()
        signing_keys = {f"{key.selector}._domainkey.{key.domain}".lower() for key in keys}
    for rec in records:
        if rec is apex_ns:
            continue
        name = _record_name(rec, zone)
        if rec.get("type") == "TXT" and name in signing_keys:
            # Recreating the zone or mail domain published the key the panel
            # signs with now; the saved record holds the discarded old key.
            continue
        try:
            # Manifest records carry the FULL record name (that's what
            # list_records returns); set_record wants the label relative to
            # the zone. Passing the FQDN straight through created
            # "www.example.com.example.com" records -- pre-existing bug fixed
            # as part of the Cloudflare work (docs/PLAN-cloudflare.md SS2).
            handlers_dns.set_record({
                "domain": zone,
                "subdomain": label_within_zone(name, zone),
                "type": rec.get("type"),
                "values": rec.get("values"),
                "ttl": rec.get("ttl", 3600),
                "proxied": rec.get("proxied", False),
            })
        except Exception as exc:  # noqa: BLE001
            _note_not_restored(summary, f"DNS record {name} {rec.get('type')}", exc)


def _refresh_restored_sites(username: str, names: list[str] | None) -> None:
    """Re-grant web server access to restored docroots; re-provision vhosts.

    Run after home.tar.gz is extracted, for every domain of a reactivated
    account (names=None) or the domains this restore recreated: a plain tar
    archive doesn't capture POSIX ACLs, so the "nobody" grant OLS's worker
    process needs could be missing from a restored docroot (found live -- a
    404 with no obvious cause until getfacl showed the grant was simply never
    there, even with a correctly configured vhost pointed right at it).
    Domain rows that predate this restore (kept by releases that did not
    release them at termination) never had their vhost recreated by
    add_domain either, so every vhost is provisioned again.
    """
    from daemon import handlers_domain, ols

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        rows = session.scalars(select(Domain).where(Domain.account_id == account.id).order_by(Domain.id)).all()
        targets = [(row.domain, row.docroot) for row in rows if names is None or row.domain in names]
        account_snapshot = account
    for name, docroot in targets:
        try:
            handlers_domain.ensure_docroot(username, docroot, domain_name=name)
        except Exception:  # noqa: BLE001
            logger.exception("failed to restore docroot permissions for '%s' (account '%s')", name, username)
    try:
        ols.provision_vhost(account_snapshot)
    except Exception:  # noqa: BLE001
        logger.exception("failed to (re)provision vhosts for restored account '%s'", username)


def restore_outcome(summary: dict, done: str = "completed") -> tuple[str, str | None]:
    """The final progress line of a full restore, and the details to keep
    on its job row when something needs attention (None when nothing does).

    Job rows have no structured summary field, so the details are text.
    """
    details = []
    if summary.get("skipped_domains"):
        details.append("Skipped because another account uses them now: "
                       + ", ".join(summary["skipped_domains"]) + ".")
    for entry in summary.get("not_restored") or []:
        details.append(f"Not restored: {entry['item']} ({entry['reason']}).")
    if summary.get("mailboxes_requiring_password_reset"):
        details.append("Recreated mailboxes have new random passwords (backups never store passwords); "
                       "set new ones for: " + ", ".join(summary["mailboxes_requiring_password_reset"]) + ".")
    if summary.get("database_users_requiring_password_reset"):
        details.append("Recreated database logins have new random passwords; set new ones and update "
                       "the site configuration for: "
                       + ", ".join(summary["database_users_requiring_password_reset"]) + ".")
    if not details:
        return done, None
    text = " ".join(details)
    if len(text) > 4000:
        text = text[:3999] + "…"
    return f"{done} with warnings", text


@serialized_worker
def _restore_full(
    username: str,
    account_status: str,
    local_artifact: str,
    tmp_dir: str,
    restore_job_id: int | None = None,
    progress=None,
    reactivation: dict | None = None,
) -> dict:
    """Restore a full backup onto an active account, or recreate a
    terminated one from it.

    ``reactivation["done"]`` is set once this call has made a terminated
    account active again, so a caller whose restore then fails terminates
    only an account its own restore brought back (terminate_after_failed_
    recreation), never one another restore or import reactivated meanwhile.

    Every domain, database (with its logins), mail domain, mailbox and cron
    job in the backup that the account no longer has is recreated before
    the files, databases and mail are put back -- so a deleted mailbox
    comes back as a working mailbox, not only as its message files. The
    saved DNS records are applied to a zone this restore recreates; a zone
    that still exists is the account's current DNS and is left alone.
    Hostnames another account holds now are skipped. Anything that could
    not be recreated is listed in the returned summary (restore_outcome)
    instead of being dropped silently or failing the whole restore.
    """
    def report(message: str) -> None:
        if progress is not None:
            progress(message)
        elif restore_job_id is not None:
            _update_restore(restore_job_id, progress_message=message)

    summary = {
        "initial_password": None,
        "skipped_domains": [],
        "not_restored": [],
        "mailboxes_requiring_password_reset": [],
        "database_users_requiring_password_reset": [],
    }
    report("extracting backup")
    content_dir, manifest = _full_artifact_content(username, local_artifact, tmp_dir)
    foreign = _foreign_names(username, manifest)
    summary["skipped_domains"] = sorted(foreign)
    reactivated = account_status in ("terminated", "error")
    dns_zone = manifest.get("dns_zone") or None
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        zone_existed = dns_zone is not None and session.scalar(select(DnsZone.id).where(
            DnsZone.account_id == account.id, DnsZone.zone == dns_zone["zone"])) is not None

    if reactivated:
        # reactivate_account, not create_account: terminating an account
        # never deletes its historical DB row (see terminate_account), so
        # create_account's own "already exists" guard would always fire
        # here -- found live when that got silently treated as a harmless
        # no-op, the Linux user was never actually recreated, and the
        # restore failed several steps later trying to chown files to a
        # uid that no longer existed.
        report("recreating account")
        recreated = _reactivate_for_restore(username, {
            "username": username,
            "php_version": manifest.get("php_version"),
            "quota_soft_mb": manifest.get("quota_soft_mb"),
            "quota_hard_mb": manifest.get("quota_hard_mb"),
            "cpu_pct": manifest.get("cpu_pct"),
            "mem_mb": manifest.get("mem_mb"),
            "io_mb": manifest.get("io_mb"),
            "pids_max": manifest.get("pids_max"),
        })
        summary["initial_password"] = recreated.get("initial_password")
        if reactivation is not None:
            reactivation["done"] = True

    # Termination released the account's domains (another tenant may hold
    # one now: skipped above), so on a reactivated account add_domain
    # recreates every one with its docroot and vhost; on an active account
    # it recreates the ones removed since the backup.
    report("recreating domains")
    recreated_domains = _recreate_domains(username, manifest, foreign, summary)
    report("recreating databases")
    recreated_databases = _recreate_databases(username, manifest, summary)
    _recreate_database_users(username, manifest, recreated_databases, summary)
    report("recreating mail")
    _recreate_mail(username, manifest, foreign, summary)
    report("recreating cron jobs")
    _recreate_cron_jobs(username, manifest, summary)
    if dns_zone is not None and dns_zone["zone"] not in foreign:
        report("recreating DNS zone")
        _recreate_dns_zone(username, dns_zone, reactivated or not zone_existed, summary)

    report("restoring files")
    home_tar = content_dir / "home.tar.gz"
    if home_tar.exists():
        pw = _pwnam(username)
        _restore_tree(home_tar, Path(settings.home_base) / username, pw, prefix=username)

    if reactivated or recreated_domains:
        report("restoring docroot permissions")
        _refresh_restored_sites(username, None if reactivated else recreated_domains)

    report("restoring databases")
    db_dir = content_dir / "databases"
    if db_dir.exists():
        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == username))
            owned = set(session.scalars(select(DatabaseGrant.db_name).where(DatabaseGrant.account_id == account.id)).all())
        for dump in sorted(db_dir.glob("*.sql.gz")):
            db_name = dump.name[: -len(".sql.gz")]
            if db_name not in owned:
                # Never restored into: it could not be recreated (reported
                # above), or the archive names a database of no account here.
                if not _noted(summary, f"database {db_name}"):
                    summary["not_restored"].append({"item": f"database {db_name}",
                                                    "reason": "it is not one of this account's databases"})
                continue
            _restore_database_dump(db_name, dump)

    report("restoring mail")
    mail_dir = content_dir / "mail"
    if mail_dir.exists():
        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == username))
            owned = set(session.scalars(select(MailDomain.domain).where(MailDomain.account_id == account.id)).all())
        for tarball in sorted(mail_dir.glob("*.tar.gz")):
            domain = tarball.name[: -len(".tar.gz")]
            validate_domain(domain)
            if domain in foreign:
                continue  # listed with the skipped names
            if domain not in owned:
                if not _noted(summary, f"mail domain {domain}"):
                    summary["not_restored"].append({"item": f"mail for {domain}",
                                                    "reason": "it is not one of this account's mail domains"})
                continue
            _restore_tree(tarball, Path(settings.mail_base) / domain, _pwnam('vmail'), prefix=domain)
    return summary


def terminate_after_failed_recreation(username: str) -> str:
    """Terminate an account that a failed restore or import reactivated.

    A half-restored account must not stay active: its sites, mail and
    logins would be live with only part of their data. Returns the sentence
    the failed job should add to its error ('' when the account was not
    reactivated, so there is nothing to undo).
    """
    from daemon import handlers_account

    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        status = account.status if account is not None else None
    if status != "active":
        return ""
    try:
        handlers_account.terminate_account({"username": username})
    except Exception:  # noqa: BLE001
        logger.exception("could not terminate '%s' again after a failed restore", username)
        return ("The account was recreated but could not be terminated again; "
                "terminate it from the account page before retrying.")
    return "The partly restored account was terminated again."


def _restore_file(username: str, item_ref: str, local_artifact: str, restore_job_id: int) -> None:
    _update_restore(restore_job_id, progress_message=f"restoring {item_ref}")
    home_dir = Path(settings.home_base) / username
    if not home_dir.exists():
        raise BackupError(f"account '{username}' has no home directory to restore into")
    pw = _pwnam(username)
    _restore_tree(local_artifact, home_dir, pw)


def _trusted_python_interpreter_link(member, destination: Path) -> bool:
    from shared.archive_links import standard_python_venv_link
    return member.issym() and standard_python_venv_link(member.name, member.linkname, destination)


def _restore_tree(artifact, destination, owner, prefix=None, select=None):
    """Validate a private archive copy, then extract with destination privileges.

    `select`, a relative path after `prefix` is stripped, extracts only that
    file or directory subtree. Every member is still validated.
    """
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
        selected = 0
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
                    if not _trusted_python_interpreter_link(checked, Path(destination)):
                        raise BackupError("unsafe restore archive path or link") from exc
                if select is not None:
                    name = Path(checked.name).as_posix()
                    if name != select and not name.startswith(select + "/"):
                        continue
                    selected += 1
                expanded += member.size if member.isreg() else 0
                if expanded > settings.cpanel_import_max_extracted_bytes:
                    raise BackupError("restore archive exceeds the expansion limit")
        if select is not None and not selected:
            raise BackupError(f"'{select}' is not in this backup")
        with private.open('rb') as stream:
            compressed = stream.read(2) == b'\x1f\x8b'
        argv = ['tar', '-xz' if compressed else '-x', '-f', '-', '--no-same-owner', '--no-same-permissions', '-C', str(destination)]
        if prefix is not None:
            argv.append('--strip-components=1')
        if select is not None:
            # tar matches member names before stripping components, and
            # literally: a path containing *, ? or [ is never a pattern.
            argv += ['--no-wildcards', '--', f"{prefix}/{select}" if prefix else select]
        result = run(argv, input_path=str(private), uid=owner.pw_uid, gid=owner.pw_gid, timeout=600)
        if not result.ok:
            raise BackupError("account-privileged archive extraction failed")


def _restore_database(item_ref: str, local_artifact: str, restore_job_id: int) -> None:
    _update_restore(restore_job_id, progress_message=f"restoring database {item_ref}")
    _restore_database_dump(item_ref, Path(local_artifact))


def _restore_databases(username: str, local_artifact: str, tmp_dir: str, restore_job_id: int) -> None:
    extract_dir = Path(tmp_dir) / "database-set"
    extract_dir.mkdir()
    _safe_extract_tar(local_artifact, extract_dir)
    _restore_database_dumps(username, extract_dir / "databases", restore_job_id)


def _restore_database_dumps(username: str, dump_dir: Path, restore_job_id: int) -> None:
    account_id = _resolve_account_id(username)
    with write_session() as session:
        owned = set(session.scalars(select(DatabaseGrant.db_name).where(DatabaseGrant.account_id == account_id)).all())
    dumps = sorted(dump_dir.glob("*.sql.gz")) if dump_dir.is_dir() else []
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
    guard = ExitStack()
    username = account_status = None
    reactivation = {}
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
            _require_restorable(account.status, restore_job.kind, backup_job.kind)
            username = account.username
            account_status = account.status
            kind = restore_job.kind
            item_ref = restore_job.item_ref
            backup_kind = backup_job.kind
            _authorize_item(session, account, kind, item_ref)
            artifact_path = backup_job.artifact_path
            dest_kind = destination.kind
            restore_job.status = "running"
            restore_job.progress_message = "fetching backup artifact"

        from daemon.snapshot_jobs import lock
        # The settings handlers a full restore calls (account reactivation,
        # domain.add, cron) take this same lock re-entrantly; any other
        # caller is refused while the restore runs.
        guard.enter_context(lock(f'account-{account.id}', allow_nested=True))
        with write_session() as session:
            current = session.get(Account, account.id)
            if current is None or current.username != username or current.status != account_status:
                raise BackupError('Restore account changed while waiting to start')
            _authorize_item(session, current, kind, item_ref)
        tmp_dir = tempfile.mkdtemp(dir=settings.backup_staging_dir, prefix=f"restore-{restore_job_id}-")
        local_artifact = _fetch_artifact_locally(artifact_path, dest_kind, tmp_dir)

        outcome, details = "completed", None
        if kind == "full":
            summary = _restore_full(username, account_status, local_artifact, tmp_dir, restore_job_id,
                                    reactivation=reactivation)
            outcome, details = restore_outcome(summary)
            if summary.get("initial_password"):
                # Revealed once by an archive import; a restore job has no
                # sealed field for it, so the operator sets a new one.
                note = "The account was recreated with a new password; set one from the account page."
                outcome = "completed with warnings"
                details = f"{note} {details}" if details else note
        elif backup_kind == "full":
            _restore_item_from_full(username, kind, item_ref, local_artifact, tmp_dir, restore_job_id)
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

        shutil.rmtree(tmp_dir, ignore_errors=True)
        tmp_dir = None
        guard.close()
        _update_restore(restore_job_id, status="completed", progress_message=outcome, error=details,
                        completed_at=utcnow())
    except Exception as exc:  # noqa: BLE001
        error = _job_error(exc, f"restore job {restore_job_id}")
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            tmp_dir = None
        guard.close()
        _update_restore(restore_job_id, status="failed", error=error, progress_message="failed", completed_at=utcnow())
        if reactivation.get("done"):
            # After the job row stops being active and the account lock is
            # released: termination refuses to run while either is held.
            note = terminate_after_failed_recreation(username)
            if note:
                _update_restore(restore_job_id, error=f"{error} {note}"[:4000])
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        guard.close()


@contextlib.contextmanager
def _local_directory(value):
    """Pin each component under a root-owned, immutable-to-tenants ancestor."""
    from daemon.snapshot_storage import validate_local_destination
    path = validate_local_destination(value)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in path.parts[1:]:
            try:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(component, 0o700, dir_fd=fd)
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if info.st_uid != os.geteuid() or info.st_mode & 0o022:
                raise BackupError("Backup directory is writable by another identity")
        yield fd
    finally:
        os.close(fd)


def _publish_local_artifact(destination, name, source):
    if Path(name).name != name or name in (".", ".."):
        raise BackupError("Invalid backup artifact name")
    temporary = ".boron-" + secrets.token_hex(16)
    with _local_directory(destination) as directory:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as output, Path(source).open("rb") as incoming:
                shutil.copyfileobj(incoming, output, 1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            # linkat publishes without overwriting a pre-existing name/symlink.
            os.link(temporary, name, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
            os.fsync(directory)
        finally:
            os.unlink(temporary, dir_fd=directory)
    Path(source).unlink()
    return str(Path(destination) / name)
