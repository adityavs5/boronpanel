"""Cross-server export and restore for Boron's portable account archive."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import pwd
import shutil
import tarfile
import tempfile
import time
import stat
from shared.request_context import ContextThreadPoolExecutor as ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, AccountArchiveImportJob, BackupDestination, BackupJob, PanelUser, utcnow
from shared.validation import NotFoundError, ValidationError, validate_username

from daemon import backup, jobcredentials
from daemon.archive_limits import ArchiveMetadataTooLarge, open_guarded_tar

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="account-archive")


logger = logging.getLogger(__name__)

# An absolute host path inside an error message (staging files, homes).
_HOST_PATH_RE = re.compile(r"(?<![\w.:/])/(?:[\w.@+-]+/)*[\w.@+-]+/?")


class PortableArchiveError(ValidationError):
    pass


def public_import_error(exc: BaseException) -> str:
    """The text an import job may show: never a host path or internals.

    Validation failures keep their explanation with any absolute path
    replaced. Anything else is logged in full under a short reference and
    reported only by that reference.
    """
    if isinstance(exc, ValidationError):
        return _HOST_PATH_RE.sub("[server path]", str(exc))[:1000]
    reference = secrets.token_hex(8)
    logger.error("account archive import failed (reference: %s)", reference, exc_info=exc)
    return f"The archive could not be restored because of an internal error (reference: {reference})"


def _export_base() -> Path:
    """Shared daemon/API handoff outside each service's PrivateTmp."""
    root = Path(settings.snapshot_download_dir)
    base = root / "portable-exports"
    if not base.is_absolute() or ".." in base.parts or base == Path("/"):
        raise PortableArchiveError("Invalid portable export directory")
    for path in [*reversed(base.parents), base]:
        if path.is_symlink():
            raise PortableArchiveError("Portable export directory cannot use symbolic links")
    root.mkdir(parents=True, exist_ok=True, mode=0o710)
    if root.stat().st_uid != os.geteuid():
        raise PortableArchiveError("Portable export directory has an unexpected owner")
    api_user = pwd.getpwnam("boron-api")
    os.chown(root, os.geteuid(), api_user.pw_gid)
    root.chmod(0o710)
    base.mkdir(exist_ok=True, mode=0o710)
    if base.stat().st_uid != os.geteuid():
        raise PortableArchiveError("Portable export directory has an unexpected owner")
    os.chown(base, os.geteuid(), api_user.pw_gid)
    base.chmod(0o710)
    return base


def _upload_base() -> Path:
    """PrivateTmp-independent upload handoff prepared by the root daemon."""
    root = Path(settings.cpanel_import_staging_dir)
    base = root / "uploads"
    if not base.is_absolute() or ".." in base.parts or base == Path("/"):
        raise PortableArchiveError("Invalid portable import upload directory")
    for path in [*reversed(base.parents), base]:
        if path.is_symlink():
            raise PortableArchiveError("Portable import upload directory cannot use symbolic links")
    root.mkdir(parents=True, exist_ok=True, mode=0o710)
    if root.stat().st_uid != os.geteuid():
        raise PortableArchiveError("Portable import upload directory has an unexpected owner")
    api_user = pwd.getpwnam("boron-api")
    os.chown(root, os.geteuid(), api_user.pw_gid)
    root.chmod(0o710)
    base.mkdir(exist_ok=True, mode=0o710)
    if base.stat().st_uid != os.geteuid():
        raise PortableArchiveError("Portable import upload directory has an unexpected owner")
    os.chown(base, os.geteuid(), api_user.pw_gid)
    base.chmod(0o710)
    return base


def reserve_upload(_params: dict | None = None) -> dict:
    base = _upload_base()
    fd, name = tempfile.mkstemp(prefix="boron-archive-import-", suffix=".boron.tar", dir=base)
    try:
        api_user = pwd.getpwnam("boron-api")
        os.fchmod(fd, 0o600)
        os.fchown(fd, api_user.pw_uid, api_user.pw_gid)
    finally:
        os.close(fd)
    path = Path(name)
    return {"path": str(path), "upload_id": path.name}


def cleanup_upload(params: dict) -> dict:
    import re
    upload_id = str(params.get("upload_id") or "")
    if not re.fullmatch(r"boron-archive-import-[A-Za-z0-9_-]{6,80}\.boron\.tar", upload_id):
        raise ValidationError("Invalid portable import upload identifier")
    base = _upload_base()
    target = base / upload_id
    if target.is_symlink() or target.parent != base:
        raise ValidationError("Invalid portable import upload identifier")
    target.unlink(missing_ok=True)
    return {"removed": not target.exists()}


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
    expected_parent = Path(settings.cpanel_import_staging_dir) / "uploads"
    if source_path.parent != expected_parent:
        valid_source = False
    else:
        try:
            info = source_path.lstat()
            api_user = pwd.getpwnam("boron-api")
            valid_source = (
                source_path.parent == _upload_base()
                and source_path.name.startswith("boron-archive-import-")
                and source_path.name.endswith(".boron.tar")
                and stat.S_ISREG(info.st_mode)
                and not source_path.is_symlink()
                and info.st_uid == api_user.pw_uid
                and not info.st_mode & 0o077
            )
        except (FileNotFoundError, KeyError):
            valid_source = False
    if not valid_source:
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
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        job = session.get(AccountArchiveImportJob, job_id)
        if job is None:
            raise PortableArchiveError(f"archive import job {job_id} not found")
        password = jobcredentials.reveal(job.initial_password)
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
        with open_guarded_tar(path) as archive:
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
    except (tarfile.TarError, tarfile.FilterError, ArchiveMetadataTooLarge) as exc:
        raise PortableArchiveError(f"Nested component {path.name} is not a safe tar archive: {exc}") from exc


def validate_archive(archive_path: Path, extract_dir: Path, expected_username: str) -> tuple[dict, Path]:
    from daemon.cpanel_import import CpanelImportError, _extract_archive

    try:
        _extract_archive(archive_path, extract_dir)
    except CpanelImportError as exc:
        if isinstance(exc.__cause__, (tarfile.TarError, ArchiveMetadataTooLarge)):
            raise PortableArchiveError("The uploaded file is not a Boron archive") from exc
        raise PortableArchiveError(str(exc)) from exc
    root = _archive_root(extract_dir)
    try:
        manifest_path = root / "manifest.json"
        if manifest_path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("manifest too large")
        manifest = json.loads(manifest_path.read_text())
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
    reactivation = {}
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
            reactivation=reactivation,
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
        outcome, details = backup.restore_outcome(summary, done="restored")
        _update(
            job_id,
            status="completed",
            progress_message=outcome,
            error=details,
            initial_password=jobcredentials.seal(summary.get("initial_password")),
            completed_at=utcnow(),
        )
    except Exception as exc:  # noqa: BLE001
        error = public_import_error(exc)
        # Nothing half-restored stays active: a failure after this import
        # reactivated the account terminates it again (only then: an
        # account another restore brought back meanwhile is left alone).
        cleanup = backup.terminate_after_failed_recreation(username) if reactivation.get("done") else ""
        if placeholder_created and not cleanup:
            # Never reactivated, so the row this import added has nothing
            # attached to it yet.
            with write_session() as session:
                account = session.scalar(select(Account).where(Account.username == username))
                if account is not None and account.status == "terminated":
                    session.delete(account)
        _update(job_id, status="failed", progress_message="failed",
                error=f"{error} {cleanup}".strip()[:4000], completed_at=utcnow())
    finally:
        Path(source_ref).unlink(missing_ok=True)
        if work_dir is not None:
            shutil.rmtree(work_dir, ignore_errors=True)


# A handoff copy is opened by boron-api as soon as prepare returns and is
# removed when its response finishes. A client that disconnects mid-stream
# can skip that cleanup; such copies are swept once nothing has written to
# them for this long (a stream in progress keeps reading its open file).
EXPORT_STALE_SECONDS = 15 * 60
# Free space a prepared download must leave on the handoff filesystem.
EXPORT_FREE_RESERVE_BYTES = 2 * 1024 * 1024 * 1024
# A plain file name: never "." or "..", which would name the handoff itself.
_ARTIFACT_NAME_RE = re.compile(r"[A-Za-z0-9_@-][A-Za-z0-9_.@-]{0,199}")


def _sweep_stale_exports(base: Path) -> None:
    cutoff = time.time() - EXPORT_STALE_SECONDS
    for child in base.iterdir():
        try:
            info = child.lstat()
            if not child.name.startswith("export-") or not stat.S_ISDIR(info.st_mode):
                continue
            latest = max([info.st_mtime, *(entry.lstat().st_mtime for entry in child.iterdir())])
        except FileNotFoundError:
            continue
        if latest < cutoff:
            shutil.rmtree(child, ignore_errors=True)


def _prepare_export(prefix: str, artifact_path: str, destination_kind: str, size_bytes: int | None,
                    filename: str | None = None) -> dict:
    """Copy a backup artifact into the API handoff; bounded by free space."""
    base = _export_base()
    _sweep_stale_exports(base)
    if shutil.disk_usage(base).free < max(0, int(size_bytes or 0)) + EXPORT_FREE_RESERVE_BYTES:
        raise PortableArchiveError("Not enough free space is available on the server to prepare this download")
    temp_dir = tempfile.mkdtemp(prefix=prefix, dir=base)
    try:
        local_path = Path(backup._fetch_artifact_locally(artifact_path, destination_kind, temp_dir))
        export_path = Path(temp_dir) / (filename or local_path.name)
        if local_path != export_path:
            if local_path.parent == export_path.parent:
                os.replace(local_path, export_path)
            else:
                shutil.copy2(local_path, export_path)
        api_user = pwd.getpwnam("boron-api")
        # API and daemon use separate systemd PrivateTmp namespaces. Publish
        # into an explicit shared handoff with group traversal but no listing.
        # Cleanup stays privileged and accepts only this random child name.
        os.chown(temp_dir, os.geteuid(), api_user.pw_gid)
        os.chmod(temp_dir, 0o710)
        os.chown(export_path, api_user.pw_uid, api_user.pw_gid)
        os.chmod(export_path, 0o640)
        return {
            "path": str(export_path), "filename": export_path.name,
            "cleanup_dir": temp_dir, "cleanup_id": Path(temp_dir).name,
        }
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise


def prepare_download(params: dict) -> dict:
    job_id = int(params["job_id"])
    with write_session() as session:
        job = session.get(BackupJob, job_id)
        if job is None or job.kind != "full" or job.status != "completed" or not job.artifact_path:
            raise PortableArchiveError("A completed full-account backup is required")
        destination = session.get(BackupDestination, job.destination_id)
        artifact_path = job.artifact_path
        destination_kind = destination.kind
        size_bytes = job.size_bytes
        username = session.get(Account, job.account_id).username
    return _prepare_export("export-", artifact_path, destination_kind, size_bytes, f"{username}.boron.tar")


def _account_export_prefix(account_id: int) -> str:
    return f"export-a{int(account_id)}-"


def prepare_account_download(params: dict) -> dict:
    """Hand one of an account's own completed backups to the API to stream.

    The account-scoped counterpart of prepare_download (full archives, for
    administrators) on the same handoff, with the same cleanup and free
    space rule, plus one download at a time per account: a customer cannot
    fill the shared handoff with copies.
    """
    from daemon.snapshot_jobs import lock

    username = validate_username(params["username"])
    job_id = int(params["job_id"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        job = session.get(BackupJob, job_id)
        # Another account's job is reported exactly like a missing one.
        if account is None or job is None or job.account_id != account.id:
            raise NotFoundError(f"backup job {job_id} not found")
        if job.status != "completed" or not job.artifact_path:
            raise PortableArchiveError("Only a completed backup can be downloaded")
        destination = session.get(BackupDestination, job.destination_id)
        if destination is None:
            raise PortableArchiveError("This backup's destination no longer exists")
        artifact_path, destination_kind, size_bytes = job.artifact_path, destination.kind, job.size_bytes
        account_id = account.id
    name = PurePosixPath(artifact_path.split(":", 1)[-1] if destination_kind != "local" else artifact_path).name
    if not _ARTIFACT_NAME_RE.fullmatch(name):
        name = f"{username}-backup-{job_id}"
    prefix = _account_export_prefix(account_id)
    busy = "A download of this account's backups is already in progress. Try again when it has finished."
    try:
        with lock(f"backup-download-{account_id}", blocking=False):
            base = _export_base()
            _sweep_stale_exports(base)
            if any(child.name.startswith(prefix) for child in base.iterdir()):
                raise PortableArchiveError(busy)
            return _prepare_export(prefix, artifact_path, destination_kind, size_bytes, name)
    except BlockingIOError:
        raise PortableArchiveError(busy) from None


def cleanup_account_download(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise NotFoundError(f"account '{username}' not found")
        prefix = _account_export_prefix(account.id)
    cleanup_id = str(params.get("cleanup_id") or "")
    if not re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9_]{6,80}", cleanup_id):
        raise ValidationError("Invalid backup download cleanup identifier")
    base = _export_base()
    target = base / cleanup_id
    if target.is_symlink() or target.parent != base:
        raise ValidationError("Invalid backup download cleanup identifier")
    shutil.rmtree(target, ignore_errors=True)
    return {"removed": not target.exists()}


def cleanup_download(params: dict) -> dict:
    import re
    cleanup_id = str(params.get("cleanup_id") or "")
    if not re.fullmatch(r"export-[A-Za-z0-9_-]{6,80}", cleanup_id):
        raise ValidationError("Invalid portable export cleanup identifier")
    base = _export_base()
    target = base / cleanup_id
    if target.is_symlink() or target.parent != base:
        raise ValidationError("Invalid portable export cleanup identifier")
    shutil.rmtree(target, ignore_errors=True)
    return {"removed": not target.exists()}
