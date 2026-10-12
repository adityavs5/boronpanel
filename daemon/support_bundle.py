"""One-time, bounded and aggressively redacted administrator support bundles."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import json
import os
import re
import secrets
import stat
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import select

from daemon import audit, diagnostics
from shared.config import settings
from shared.db import write_session
from shared.models import (
    Account,
    ActionStep,
    AuditLog,
    Domain,
    PanelUser,
    SupportBundleJob,
    UpdateJob,
    utcnow,
)
from shared.request_context import ContextThreadPoolExecutor, current_request_id, request_context
from shared.validation import ValidationError
from version import BORON_VERSION

_executor = ContextThreadPoolExecutor(max_workers=1, thread_name_prefix="support-bundle")
_CONFIG_ALLOWLIST = (
    "home_base", "vhost_conf_dir", "lsphp_base", "db_path", "backup_dir", "log_dir",
    "api_bind_host", "api_bind_port", "api_customer_port", "panel_hostname",
    "php_versions", "default_php_version", "default_quota_soft_mb", "default_quota_hard_mb",
    "webmail_hostname", "mail_hostname", "pma_hostname", "cgroup_io_device",
    "snapshot_cpu_threads", "backup_concurrency", "resource_sample_retention_days",
    "audit_retention_days", "audit_step_retention_days", "support_bundle_retention_days",
    "node_versions", "default_node_version", "redis_default_mem_mb", "default_dns_provider",
)
_SENSITIVE_KEY_PARTS = (
    "password", "secret", "token", "credential", "private_key", "authorization",
    "session", "content", "body", "password_hash",
)
_MAX_LOG_FILES = 64
_MAX_LOG_TAIL_BYTES = 64 * 1024
_MAX_FAILED_ACTIONS = 50
_MAX_UPDATE_ROWS = 30


def _owner(params: dict) -> tuple[int, str]:
    ident = params.get("_authenticated_user_id")
    username = str(params.get("_authenticated_username") or "")
    if not isinstance(ident, int) or isinstance(ident, bool) or ident <= 0 or not username:
        raise ValidationError("An administrator session is required for support bundles")
    with write_session() as db:
        user = db.get(PanelUser, ident)
        if user is None or user.role != "admin" or user.disabled or user.username != username:
            raise ValidationError("The administrator account is unavailable")
    return ident, username


def _job_dict(row: SupportBundleJob) -> dict:
    return {
        "id": row.id,
        "request_id": row.request_id,
        "created_by": row.created_by_username,
        "include_names": row.include_names,
        "status": row.status,
        "progress_message": row.progress_message,
        "file_size": row.file_size,
        "manifest": row.manifest or [],
        "error": row.error,
        "created_at": row.created_at.isoformat(),
        "ready_at": row.ready_at.isoformat() if row.ready_at else None,
        "expires_at": row.expires_at.isoformat(),
        "consumed_at": row.consumed_at.isoformat() if row.consumed_at else None,
        "download_available": row.status == "ready" and row.consumed_at is None,
    }


def _bundle_root() -> Path:
    root = Path(settings.support_bundle_dir)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    return root.resolve()


def _safe_archive(path_text: str | None) -> Path | None:
    if not path_text:
        return None
    root = _bundle_root()
    candidate = Path(path_text)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        metadata = candidate.lstat()
    except (OSError, ValueError):
        return None
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        return None
    return resolved


def prune_expired(params: dict | None = None) -> dict:
    now = utcnow()
    removed = 0
    with write_session() as db:
        rows = db.scalars(select(SupportBundleJob).where(
            SupportBundleJob.expires_at <= now,
            SupportBundleJob.status.notin_(("consumed", "expired")),
        ).order_by(SupportBundleJob.id).limit(100)).all()
        for row in rows:
            archive = _safe_archive(row.file_path)
            if archive is not None:
                try:
                    archive.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
            row.file_path = None
            row.status = "expired"
            row.progress_message = "Expired"
    return {"expired": len(rows), "files_removed": removed}


def _name_replacements(db, salt: bytes, include_names: bool) -> list[tuple[str, str]]:
    if include_names:
        return []
    values: list[tuple[str, str]] = []
    for username in db.scalars(select(Account.username)).all():
        digest = hashlib.sha256(salt + username.encode()).hexdigest()[:12]
        values.append((username, f"account-{digest}"))
    for domain in db.scalars(select(Domain.domain)).all():
        digest = hashlib.sha256(salt + domain.encode()).hexdigest()[:12]
        values.append((domain, f"domain-{digest}"))
    return sorted(values, key=lambda item: len(item[0]), reverse=True)


def _safe_text(value: object, replacements: list[tuple[str, str]], *, limit: int = 64 * 1024) -> str:
    text = audit.redact_diagnostic_text(value, max_bytes=limit)
    for original, replacement in replacements:
        text = re.sub(re.escape(original), replacement, text, flags=re.IGNORECASE)
    return text


def _safe_value(value, replacements: list[tuple[str, str]], key: str = ""):
    lowered = key.lower()
    if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
        return "***"
    if isinstance(value, dict):
        return {str(child_key): _safe_value(child, replacements, str(child_key))
                for child_key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, replacements, key) for item in value]
    if isinstance(value, str):
        return _safe_text(value, replacements)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _safe_text(str(value), replacements)


def _tail_regular_file(path: Path) -> str:
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            return ""
        with path.open("rb") as handle:
            handle.seek(max(0, metadata.st_size - _MAX_LOG_TAIL_BYTES))
            data = handle.read(_MAX_LOG_TAIL_BYTES)
    except OSError:
        return ""
    return data.decode("utf-8", "replace")


def _log_entries(replacements: list[tuple[str, str]]) -> dict[str, str]:
    root = Path(settings.log_dir)
    entries: dict[str, str] = {}
    try:
        candidates = sorted(root.glob("*.log"))[:_MAX_LOG_FILES]
    except OSError:
        return entries
    for path in candidates:
        content = _tail_regular_file(path)
        if content:
            entries[f"logs/{path.name}.tail.txt"] = _safe_text(content, replacements, limit=_MAX_LOG_TAIL_BYTES)
    return entries


def _error_references(log_entries: dict[str, str]) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    allowed = ("reference", "request_id", "status", "method", "path", "error_type", "timestamp", "occurred_at")
    for content in log_entries.values():
        for line in content.splitlines()[-500:]:
            try:
                item = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(item, dict) or not isinstance(item.get("reference"), str):
                continue
            reference = item["reference"]
            if reference in seen:
                continue
            seen.add(reference)
            rows.append({key: item.get(key) for key in allowed if item.get(key) is not None})
            if len(rows) >= 100:
                return rows
    return rows


def _database_entries(replacements: list[tuple[str, str]]) -> dict[str, object]:
    with write_session() as db:
        failed = db.scalars(select(AuditLog).where(AuditLog.result == "failed")
                            .order_by(AuditLog.id.desc()).limit(_MAX_FAILED_ACTIONS)).all()
        failed_rows = []
        for row in failed:
            steps = db.scalars(select(ActionStep).where(ActionStep.audit_id == row.id)
                               .order_by(ActionStep.id)).all()
            failed_rows.append({
                "id": row.id, "created_at": row.created_at.isoformat(), "actor": row.actor,
                "role": row.role, "operation": row.op, "target": row.target,
                "result": row.result, "detail": row.detail, "request_id": row.request_id,
                "duration_ms": row.duration_ms, "changes": row.changes,
                "steps": [{
                    "name": step.name, "duration_ms": step.duration_ms, "result": step.result,
                    "exit_code": step.exit_code, "stdout_tail": step.stdout_tail,
                    "stderr_tail": step.stderr_tail,
                } for step in steps],
            })
        updates = db.scalars(select(UpdateJob).order_by(UpdateJob.id.desc()).limit(_MAX_UPDATE_ROWS)).all()
        update_rows = [{
            "id": row.id, "kind": row.kind, "status": row.status,
            "from_version": row.from_version, "to_version": row.to_version,
            "initiated_by": row.initiated_by, "progress_message": row.progress_message,
            "error": row.error, "rolled_back": row.rolled_back,
            "started_at": row.started_at.isoformat(),
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        } for row in updates]
    return {
        "failed-actions.json": _safe_value(failed_rows, replacements),
        "update-history.json": _safe_value(update_rows, replacements),
    }


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, default=str) + "\n").encode()


def _build_archive(job_id: int) -> tuple[Path, int, list[dict]]:
    with write_session() as db:
        job = db.get(SupportBundleJob, job_id)
        if job is None:
            raise RuntimeError("support bundle job disappeared")
        include_names = job.include_names
        replacements = _name_replacements(db, secrets.token_bytes(32), include_names)

    diagnostic_result = diagnostics.run_all({})
    config_data = asdict(settings)
    allowed_config = {key: config_data.get(key) for key in _CONFIG_ALLOWLIST}
    log_entries = _log_entries(replacements)
    db_entries = _database_entries(replacements)
    entries: dict[str, bytes] = {
        "build.json": _json_bytes({
            "version": BORON_VERSION,
            "python": f"{os.sys.version_info.major}.{os.sys.version_info.minor}.{os.sys.version_info.micro}",
            "release_path": str(Path("/opt/boron").resolve()),
            "generated_at": utcnow().isoformat(),
        }),
        "config.json": _json_bytes(_safe_value(allowed_config, replacements)),
        "diagnostics.json": _json_bytes(_safe_value(diagnostic_result, replacements)),
        "services.json": _json_bytes([
            item for item in diagnostic_result["checks"] if str(item.get("id", "")).startswith("service_")
        ]),
        "failed-actions.json": _json_bytes(db_entries["failed-actions.json"]),
        "update-history.json": _json_bytes(db_entries["update-history.json"]),
        "error-references.json": _json_bytes(_error_references(log_entries)),
    }
    for name, content in log_entries.items():
        entries[name] = content.encode("utf-8", "replace")

    max_archive = max(1024 * 1024, min(int(settings.support_bundle_max_bytes), 15 * 1024 * 1024))
    max_uncompressed = max_archive * 2
    if sum(len(content) for content in entries.values()) > max_uncompressed:
        raise RuntimeError("support bundle input exceeded the bounded export budget")
    manifest = [{
        "path": name,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "redaction": "allowlist" if name in ("build.json", "config.json", "services.json") else "redacted",
    } for name, content in sorted(entries.items())]
    manifest.append({"path": "manifest.json", "size": 0, "sha256": None, "redaction": "manifest"})
    # The manifest lists itself. Iterate until its encoded byte length is
    # stable; a self-hash would be recursive, so that one field is null.
    for _attempt in range(8):
        manifest_bytes = _json_bytes({"files": manifest, "include_names": include_names})
        if manifest[-1]["size"] == len(manifest_bytes):
            break
        manifest[-1]["size"] = len(manifest_bytes)
    entries["manifest.json"] = manifest_bytes

    root = _bundle_root()
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{job_id}-", suffix=".zip", dir=root)
    os.close(descriptor)
    temporary = Path(temporary_name)
    final = root / f"support-{job_id}-{secrets.token_hex(8)}.zip"
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        size = temporary.stat().st_size
        if size > max_archive:
            raise RuntimeError("support bundle archive exceeded the bounded export budget")
        os.chmod(temporary, 0o600)
        os.replace(temporary, final)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return final, size, manifest


def _run_job(job_id: int, request_id: str | None) -> None:
    with request_context(request_id):
        with write_session() as db:
            job = db.get(SupportBundleJob, job_id)
            if job is None or job.status != "queued":
                return
            job.status = "running"
            job.progress_message = "Running read-only diagnostics and collecting redacted metadata"
        try:
            path, size, manifest = _build_archive(job_id)
        except Exception as exc:  # noqa: BLE001 - background failures become safe task state
            with write_session() as db:
                job = db.get(SupportBundleJob, job_id)
                if job is not None:
                    job.status = "failed"
                    job.progress_message = "Support bundle generation failed"
                    job.error = f"Support bundle generation failed ({type(exc).__name__})"
            return
        with write_session() as db:
            job = db.get(SupportBundleJob, job_id)
            if job is None:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                return
            job.status = "ready"
            job.progress_message = "Support bundle ready for one-time download"
            job.file_path = str(path)
            job.file_size = size
            job.manifest = manifest
            job.ready_at = utcnow()


def create(params: dict) -> dict:
    owner_id, owner_name = _owner(params)
    prune_expired()
    include_names = params.get("include_names") is True
    now = utcnow()
    with write_session() as db:
        active = db.scalars(select(SupportBundleJob.id).where(
            SupportBundleJob.created_by_user_id == owner_id,
            SupportBundleJob.status.in_(("queued", "running")),
        ).limit(1)).first()
        if active is not None:
            raise ValidationError("A support bundle is already being generated for this administrator")
        job = SupportBundleJob(
            request_id=current_request_id(), created_by_user_id=owner_id,
            created_by_username=owner_name, include_names=include_names,
            status="queued", progress_message="Queued",
            created_at=now,
            expires_at=now + dt.timedelta(days=max(1, int(settings.support_bundle_retention_days))),
        )
        db.add(job)
        db.flush()
        result = _job_dict(job)
        job_id, request_id = job.id, job.request_id
    _executor.submit(_run_job, job_id, request_id)
    return result


def list_jobs(params: dict) -> dict:
    owner_id, _owner_name = _owner(params)
    prune_expired()
    with write_session() as db:
        rows = db.scalars(select(SupportBundleJob).where(
            SupportBundleJob.created_by_user_id == owner_id,
        ).order_by(SupportBundleJob.id.desc()).limit(20)).all()
        return {"jobs": [_job_dict(row) for row in rows]}


def get_job(params: dict) -> dict:
    owner_id, _owner_name = _owner(params)
    prune_expired()
    try:
        ident = int(params.get("id"))
    except (TypeError, ValueError):
        raise ValidationError("Invalid support bundle job") from None
    with write_session() as db:
        row = db.get(SupportBundleJob, ident)
        if row is None or row.created_by_user_id != owner_id:
            raise ValidationError("Support bundle job not found")
        return _job_dict(row)


def consume(params: dict) -> dict:
    owner_id, _owner_name = _owner(params)
    try:
        ident = int(params.get("id"))
    except (TypeError, ValueError):
        raise ValidationError("Invalid support bundle job") from None
    with write_session() as db:
        row = db.get(SupportBundleJob, ident)
        if row is None or row.created_by_user_id != owner_id:
            raise ValidationError("Support bundle job not found")
        if row.status != "ready" or row.consumed_at is not None:
            raise ValidationError("This support bundle is not available for download")
        archive = _safe_archive(row.file_path)
        if archive is None:
            raise ValidationError("The support bundle file is unavailable")
        data = archive.read_bytes()
        if len(data) > int(settings.support_bundle_max_bytes):
            raise ValidationError("The support bundle exceeds the download limit")
        archive.unlink()
        row.file_path = None
        row.consumed_at = utcnow()
        row.status = "consumed"
        row.progress_message = "Downloaded and removed"
        filename = f"boron-support-{row.id}.zip"
    return {"filename": filename, "content_base64": base64.b64encode(data).decode("ascii")}
