"""Cross-server export and restore for Boron's portable account archive."""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import shutil
import tarfile
import tempfile
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, AccountArchiveImportJob, BackupDestination, BackupJob, PanelUser, utcnow
from shared.validation import ValidationError, validate_username

from daemon import backup

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="account-archive")


class PortableArchiveError(Exception):
    pass


def _job_dict(job: AccountArchiveImportJob, password: str | None = None) -> dict:
    result = {
        "id": job.id,
        "username": job.username,
        "status": job.status,
        "progress_message": job.progress_message,
        "archive_version": job.archive_version,
        "components_verified": job.components_verified,
        "error": job.error,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }
    if password:
        result["initial_password"] = password
    return result


def _update(job_id: int, **fields) -> None:
    with write_session() as session:
        job = session.get(AccountArchiveImportJob, job_id)
        if job is not None:
            for key, value in fields.items():
                setattr(job, key, value)


def trigger_import(params: dict) -> dict:
    username = validate_username(params["username"])
    source_ref = str(params.get("source_ref") or "")
    if not source_ref:
        raise ValidationError("An uploaded Boron archive is required")
    source_path = Path(source_ref)
    if source_path.parent != Path("/tmp") or not source_path.name.startswith("boron-archive-import-") or source_path.is_symlink() or not source_path.is_file():
        raise ValidationError("The archive upload staging path is invalid")
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is not None and account.status not in {"terminated", "error"}:
            raise PortableArchiveError(f"account '{username}' already exists and is not terminated")
        if account is None and session.scalar(select(PanelUser).where(PanelUser.username == username)) is not None:
            raise PortableArchiveError(f"panel login '{username}' already exists -- choose another account username")
        active = session.scalar(select(AccountArchiveImportJob).where(
            AccountArchiveImportJob.username == username,
            AccountArchiveImportJob.status.in_(("pending", "running")),
        ))
        if active is not None:
            raise PortableArchiveError(f"an archive import for '{username}' is already running")
        job = AccountArchiveImportJob(username=username, source_ref=source_ref, status="pending", progress_message="queued")
        session.add(job)
        session.flush()
        job_id = job.id
        result = _job_dict(job)
    _executor.submit(_run_import, job_id, username, source_ref)
    return result


def get_job(params: dict) -> dict:
    job_id = int(params["job_id"])
    with write_session() as session:
        job = session.get(AccountArchiveImportJob, job_id)
        if job is None:
            raise PortableArchiveError(f"archive import job {job_id} not found")
        password = job.initial_password
        result = _job_dict(job, password=password)
        if password:
            job.initial_password = None
        return result


def list_jobs(params: dict | None = None) -> dict:
    with write_session() as session:
        rows = session.scalars(select(AccountArchiveImportJob).order_by(AccountArchiveImportJob.started_at.desc()).limit(50)).all()
        return {"jobs": [_job_dict(row) for row in rows]}


def _archive_root(extract_dir: Path) -> Path:
    if (extract_dir / "manifest.json").is_file():
        return extract_dir
    entries = list(extract_dir.iterdir())
    children = [item for item in entries if item.is_dir()]
    if len(entries) == 1 and len(children) == 1 and (children[0] / "manifest.json").is_file():
        return children[0]
    raise PortableArchiveError("Archive must contain one account directory with manifest.json")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_inner_tar(path: Path) -> int:
    total = 0
    try:
        with tarfile.open(path) as archive:
            count = 0
            file_names: set[str] = set()
            for member in archive:
                count += 1
                if count > backup.MAX_BACKUP_ARCHIVE_MEMBERS:
                    raise PortableArchiveError(f"Nested archive {path.name} contains too many members")
                if (len(member.name.encode("utf-8", errors="replace")) > backup.MAX_BACKUP_ARCHIVE_PATH_BYTES
                        or len(Path(member.name).parts) > backup.MAX_BACKUP_ARCHIVE_PATH_DEPTH):
                    raise PortableArchiveError(f"Nested archive {path.name} contains an excessively long or deep path")
                if member.isreg():
                    if member.name in file_names:
                        raise PortableArchiveError(f"Nested archive {path.name} contains a duplicate file path")
                    file_names.add(member.name)
                total += member.size if member.isreg() else 0
                if total > settings.cpanel_import_max_extracted_bytes:
                    raise PortableArchiveError(f"Nested archive {path.name} exceeds the extraction limit")
                # data_filter performs the same path/link/device checks that
                # extractall(filter='data') will enforce during restoration.
                tarfile.data_filter(member, path.parent)
        return total
    except (tarfile.TarError, tarfile.FilterError) as exc:
        raise PortableArchiveError(f"Nested component {path.name} is not a safe tar archive: {exc}") from exc


def validate_archive(archive_path: Path, extract_dir: Path, expected_username: str) -> tuple[dict, Path]:
    from daemon.cpanel_import import _extract_archive

    _extract_archive(archive_path, extract_dir)
    root = _archive_root(extract_dir)
    try:
        manifest = json.loads((root / "manifest.json").read_text())
    except (OSError, ValueError) as exc:
        raise PortableArchiveError("manifest.json is missing or invalid") from exc
    if manifest.get("format") != backup.PORTABLE_ARCHIVE_FORMAT:
        raise PortableArchiveError("This is not a Boron portable account archive")
    if manifest.get("format_version") != backup.PORTABLE_ARCHIVE_VERSION:
        raise PortableArchiveError(f"Unsupported Boron archive version {manifest.get('format_version')!r}")
    if validate_username(manifest.get("username", "")) != expected_username:
        raise PortableArchiveError("The target username must match the username stored in this archive")
    components = manifest.get("components")
    if not isinstance(components, list):
        raise PortableArchiveError("Archive manifest has no component checksum inventory")
    listed = set()
    nested_expanded_bytes = 0
    for component in components:
        if not isinstance(component, dict):
            raise PortableArchiveError("Archive manifest contains an invalid component entry")
        relative = PurePosixPath(str(component.get("path", "")))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise PortableArchiveError("Archive manifest contains an unsafe component path")
        relative_name = relative.as_posix()
        if relative_name in listed:
            raise PortableArchiveError(f"Archive manifest lists {relative_name} more than once")
        listed.add(relative_name)
        path = root.joinpath(*relative.parts)
        if not path.is_file() or path.is_symlink():
            raise PortableArchiveError(f"Archive component {relative_name} is missing or not a regular file")
        try:
            declared_size = int(component.get("size_bytes", -1))
        except (TypeError, ValueError) as exc:
            raise PortableArchiveError(f"Archive component {relative_name} has an invalid size") from exc
        checksum = component.get("sha256")
        if not isinstance(checksum, str) or len(checksum) != 64 or any(ch not in "0123456789abcdef" for ch in checksum.lower()):
            raise PortableArchiveError(f"Archive component {relative_name} has an invalid SHA-256 value")
        if path.stat().st_size != declared_size:
            raise PortableArchiveError(f"Archive component {relative_name} has the wrong size")
        if _hash_file(path) != checksum.lower():
            raise PortableArchiveError(f"Archive component {relative_name} failed its SHA-256 check")
        if relative_name.endswith((".tar", ".tar.gz", ".tgz")):
            nested_expanded_bytes += _validate_inner_tar(path)
            if nested_expanded_bytes > settings.cpanel_import_max_extracted_bytes:
                raise PortableArchiveError("Nested archive components exceed the total extraction limit")
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file() and path.name != "manifest.json"}
    if actual != listed:
        extra = sorted(actual - listed)
        missing = sorted(listed - actual)
        raise PortableArchiveError(f"Archive component inventory mismatch (extra={extra}, missing={missing})")
    return manifest, root


def _run_import(job_id: int, username: str, source_ref: str) -> None:
    work_dir = None
    placeholder_created = False
    try:
        with write_session() as session:
            job = session.get(AccountArchiveImportJob, job_id)
            if job is None or job.status != "pending":
                return
            username = validate_username(job.username)
            source_ref = job.source_ref
            account = session.scalar(select(Account).where(Account.username == username))
            if account is not None and account.status not in {"terminated", "error"}:
                raise PortableArchiveError(f"account '{username}' already exists and is not terminated")
            if account is None and session.scalar(select(PanelUser).where(PanelUser.username == username)) is not None:
                raise PortableArchiveError(f"panel login '{username}' already exists -- choose another account username")
            job.status = "running"
            job.progress_message = "verifying archive"

        os.makedirs(settings.cpanel_import_staging_dir, mode=0o700, exist_ok=True)
        work_dir = Path(tempfile.mkdtemp(dir=settings.cpanel_import_staging_dir, prefix=f"boron-import-{job_id}-"))
        private_archive = work_dir / "account.boron.tar"
        source_fd = os.open(source_ref, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            source_stat = os.fstat(source_fd)
            if not stat.S_ISREG(source_stat.st_mode):
                raise PortableArchiveError("Archive upload is not a regular file")
            with os.fdopen(source_fd, "rb", closefd=False) as source, private_archive.open("wb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
        finally:
            os.close(source_fd)
        Path(source_ref).unlink(missing_ok=True)
        validate_dir = work_dir / "validated"
        validate_dir.mkdir()
        manifest, _root = validate_archive(private_archive, validate_dir, username)
        _update(job_id, archive_version=manifest["format_version"], components_verified=len(manifest["components"]), progress_message="preparing account")
        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == username))
            if account is None:
                session.add(Account(username=username, status="terminated", primary_domain=None))
                placeholder_created = True
                account_status = "terminated"
            else:
                account_status = account.status
        restore_dir = work_dir / "restore"
        restore_dir.mkdir()
        summary = backup._restore_full(
            username, account_status, str(private_archive), str(restore_dir),
            progress=lambda message: _update(job_id, progress_message=message),
        )
        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == username))
            has_login = session.scalar(select(PanelUser.id).where(PanelUser.account_id == account.id)) is not None
            account_id = account.id
        if not has_login:
            from daemon import handlers_auth

            handlers_auth.create_panel_user({
                "username": username,
                "password": summary["initial_password"],
                "role": "customer",
                "account_id": account_id,
            })
        _update(
            job_id,
            status="completed",
            progress_message="restored",
            initial_password=summary.get("initial_password"),
            completed_at=utcnow(),
        )
    except Exception as exc:  # noqa: BLE001
        if placeholder_created:
            with write_session() as session:
                account = session.scalar(select(Account).where(Account.username == username))
                if account is not None and account.status == "terminated":
                    session.delete(account)
        _update(job_id, status="failed", progress_message="failed", error=str(exc), completed_at=utcnow())
    finally:
        Path(source_ref).unlink(missing_ok=True)
        if work_dir is not None:
            shutil.rmtree(work_dir, ignore_errors=True)


def prepare_download(params: dict) -> dict:
    job_id = int(params["job_id"])
    with write_session() as session:
        job = session.get(BackupJob, job_id)
        if job is None or job.kind != "full" or job.status != "completed" or not job.artifact_path:
            raise PortableArchiveError("A completed full-account backup is required")
        destination = session.get(BackupDestination, job.destination_id)
        artifact_path = job.artifact_path
        destination_kind = destination.kind
        username = session.get(Account, job.account_id).username
    temp_dir = tempfile.mkdtemp(prefix="boron-export-")
    try:
        local_path = backup._fetch_artifact_locally(artifact_path, destination_kind, temp_dir)
        export_path = Path(temp_dir) / f"{username}.boron.tar"
        if Path(local_path) != export_path:
            shutil.copy2(local_path, export_path)
        api_user = pwd.getpwnam("boron-api")
        os.chown(export_path, api_user.pw_uid, api_user.pw_gid)
        os.chmod(export_path, 0o600)
        return {"path": str(export_path), "filename": export_path.name, "cleanup_dir": temp_dir}
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
