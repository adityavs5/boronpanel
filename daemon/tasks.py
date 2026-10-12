"""Unified, ownership-scoped read model over Boron's existing job tables.

This module deliberately does not copy or replace the owning feature's rows.
It adapts them at read time so every operation keeps its existing durability,
retry rules and lifecycle implementation.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from daemon import imapsync, snapshot_jobs, stack_manager
from shared.db import read_session
from shared.models import (
    Account,
    AccountArchiveImportJob,
    ActionStep,
    AppInstallJob,
    AuditLog,
    BackupJob,
    BulkActionJob,
    CommandRun,
    ControlTask,
    CpanelImportJob,
    ImapMigrationJob,
    MalwareScanJob,
    NamespaceMigrationJob,
    PanelConfigJob,
    RestoreJob,
    SnapshotDestinationOperation,
    SnapshotDownload,
    SnapshotRestore,
    SnapshotRun,
    StackJob,
    SupportBundleJob,
    UpdateJob,
    WordPressJob,
)
from shared.validation import ValidationError

_REFERENCE = re.compile(r"reference:\s*([a-f0-9]{16})", re.IGNORECASE)
_TERMINAL = frozenset({"completed", "complete", "ready", "failed", "cancelled", "canceled"})
_SUCCESS = frozenset({"completed", "complete", "ready", "success", "succeeded"})
_FAILED = frozenset({"failed", "error"})


@dataclass(frozen=True)
class TaskSpec:
    kind: str
    label: str
    model: type
    owner: str | None = "account_id"
    username: str | None = None
    started: str = "started_at"
    completed: str = "completed_at"
    progress: str | None = "progress_message"
    admin_link: str = "/overview"
    customer_link: str | None = None


SPECS = (
    TaskSpec("backup", "Account backup", BackupJob, admin_link="/backup-jobs", customer_link="/backups"),
    TaskSpec("restore", "Account restore", RestoreJob, admin_link="/backup-jobs?tab=history", customer_link="/backups?view=restores"),
    TaskSpec("wordpress-install", "WordPress install", WordPressJob, admin_link="/wordpress", customer_link="/wordpress"),
    TaskSpec("malware-scan", "Malware scan", MalwareScanJob, admin_link="/malware", customer_link="/malware"),
    TaskSpec("app-install", "Application install", AppInstallJob, admin_link="/software/node-apps", customer_link="/apps"),
    TaskSpec("namespace-migration", "Filesystem isolation migration", NamespaceMigrationJob, owner=None, admin_link="/filesystem-isolation"),
    TaskSpec("account-import", "Account migration", CpanelImportJob, owner=None, username="username", admin_link="/import/accounts"),
    TaskSpec("bulk-action", "Bulk account action", BulkActionJob, owner=None, admin_link="/accounts"),
    TaskSpec("command-run", "Command run", CommandRun, admin_link="/accounts", customer_link="/devtools"),
    TaskSpec("imap-migration", "IMAP migration", ImapMigrationJob, admin_link="/imap-migrations", customer_link="/email/migration"),
    TaskSpec("update", "Panel update", UpdateJob, owner=None, admin_link="/updates"),
    TaskSpec("support-bundle", "Support bundle", SupportBundleJob, owner=None, started="created_at", completed="ready_at", admin_link="/diagnostics"),
    TaskSpec("snapshot", "Recovery point", SnapshotRun, admin_link="/backup-jobs?tab=history", customer_link="/backups"),
    TaskSpec("snapshot-download", "Backup download", SnapshotDownload, started="created_at", admin_link="/backup-jobs?tab=history", customer_link="/backups?view=restores"),
    TaskSpec("snapshot-restore", "Recovery point restore", SnapshotRestore, admin_link="/backup-jobs?tab=history", customer_link="/backups?view=restores"),
    TaskSpec("destination-operation", "Backup destination operation", SnapshotDestinationOperation, owner=None, admin_link="/backup-jobs?tab=destinations"),
    TaskSpec("panel-config", "Panel configuration", PanelConfigJob, owner=None, started="created_at", progress=None, admin_link="/panel-settings"),
    TaskSpec("account-archive-import", "Boron account import", AccountArchiveImportJob, owner=None, username="username", admin_link="/import/accounts"),
    TaskSpec("stack", "Stack maintenance", StackJob, owner=None, started="created_at", progress="detail", admin_link="/stack-manager"),
    TaskSpec("control-operation", "Control operation", ControlTask, started="created_at", admin_link="/accounts", customer_link="/domains"),
)
SPEC_BY_KIND = {spec.kind: spec for spec in SPECS}


def _integer(value: object, *, name: str) -> int:
    if isinstance(value, bool):
        raise ValidationError(f"Invalid {name}")
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"Invalid {name}") from None
    if result < 1:
        raise ValidationError(f"Invalid {name}")
    return result


def _iso(value: dt.datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.isoformat()


def _duration(started: dt.datetime | None, completed: dt.datetime | None) -> int | None:
    if started is None:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=dt.timezone.utc)
    end = completed or dt.datetime.now(dt.timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=dt.timezone.utc)
    return max(0, int((end - started).total_seconds()))


def _date_filter(value: object, *, end: bool = False) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError("Use an ISO date for task history filters") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    if len(text) == 10 and end:
        parsed += dt.timedelta(days=1)
    return parsed.astimezone(dt.timezone.utc)


def _status(value: object) -> str:
    status = str(value or "unknown").lower()
    if status in _SUCCESS:
        return "completed"
    if status in _FAILED:
        return "failed"
    if status in {"cancelled", "canceled"}:
        return "cancelled"
    if status in {"pending", "queued", "waiting"}:
        return "queued"
    if status in {"running", "finalizing", "preparing", "cancelling"}:
        return "running"
    return status


def _progress(row: Any, status: str) -> int | None:
    if isinstance(row, ControlTask):
        return max(0, min(100, int(row.progress_pct or 0)))
    if isinstance(row, StackJob):
        return max(0, min(100, int(row.progress_pct or 0)))
    if isinstance(row, (BulkActionJob, NamespaceMigrationJob)) and row.total:
        return max(0, min(100, int(row.completed_count * 100 / row.total)))
    if isinstance(row, ImapMigrationJob) and row.folders_total:
        return max(0, min(100, int(row.folders_done * 100 / row.folders_total)))
    if isinstance(row, UpdateJob) and row.steps:
        finished = sum(1 for step in row.steps if str(step.get("status", "")).lower() in _TERMINAL | {"ok"})
        return max(0, min(100, int(finished * 100 / len(row.steps))))
    if status in {"completed", "failed", "cancelled"}:
        return 100
    if status == "queued":
        return 0
    return None


def _reference(error: object) -> str | None:
    match = _REFERENCE.search(str(error or ""))
    return match.group(1).lower() if match else None


def _safe_detail(row: Any, spec: TaskSpec, status: str) -> str:
    value = getattr(row, spec.progress, None) if spec.progress else None
    if isinstance(value, str) and value.strip():
        return value.strip()[:500]
    if status == "failed":
        return "The task failed. Open its feature page for recovery options."
    if status == "completed":
        return f"{spec.label} completed."
    if status == "cancelled":
        return f"{spec.label} cancelled."
    return f"{spec.label} is {status}."


def _capabilities(spec: TaskSpec, row: Any, status: str) -> tuple[bool, bool]:
    retry = spec.kind == "snapshot" and status in {"failed", "cancelled"}
    cancel = (
        (spec.kind == "snapshot" and status in {"queued", "running"})
        or (spec.kind == "imap-migration" and status in {"queued", "running"})
        or (spec.kind == "stack" and status in {"queued", "running"})
    )
    return retry, cancel


def _task(spec: TaskSpec, row: Any, accounts: dict[int, str], *, role: str) -> dict:
    account_id = getattr(row, spec.owner, None) if spec.owner else None
    username = accounts.get(account_id) if account_id is not None else None
    if username is None and spec.username:
        username = str(getattr(row, spec.username, "") or "") or None
    started = getattr(row, spec.started, None)
    completed = getattr(row, spec.completed, None)
    status = _status(getattr(row, "status", None))
    retry, cancel = _capabilities(spec, row, status)
    error = getattr(row, "error", None)
    label = spec.label
    feature_url = spec.customer_link if role == "customer" and spec.customer_link else spec.admin_link
    if isinstance(row, ControlTask):
        label = {
            "account.create": "Create account",
            "account.rename": "Rename account",
            "account.terminate": "Terminate account",
            "domain.add": "Add domain",
        }.get(row.operation, spec.label)
        if row.operation == "account.create" and row.username:
            feature_url = f"/accounts/{row.username}" if status == "completed" else "/accounts"
        elif row.operation in {"account.rename", "account.terminate"}:
            feature_url = f"/accounts/{row.username}" if status == "completed" and row.operation == "account.rename" else "/accounts"
        error = f"reference: {row.error_reference}" if row.error_reference else None
    return {
        "key": f"{spec.kind}:{row.id}",
        "id": row.id,
        "kind": spec.kind,
        "label": label,
        "account": username,
        "account_id": account_id,
        "status": status,
        "progress": _progress(row, status),
        "detail": _safe_detail(row, spec, status),
        "started_at": _iso(started),
        "completed_at": _iso(completed),
        "duration_seconds": _duration(started, completed),
        "request_id": getattr(row, "request_id", None),
        "error_reference": _reference(error),
        "feature_url": feature_url,
        "can_retry": retry,
        "can_cancel": cancel,
    }


def _scope(params: dict) -> tuple[str, int | None]:
    role = str(params.get("_authenticated_role") or "")
    account_id = params.get("_authenticated_account_id")
    if role not in {"admin", "customer"}:
        raise ValidationError("Task history is available to administrators and hosting customers")
    if role == "customer" and not isinstance(account_id, int):
        raise ValidationError("Customer account is unavailable")
    return role, account_id


def _load_rows(db, spec: TaskSpec, *, limit: int = 500) -> list[Any]:
    start_column = getattr(spec.model, spec.started)
    return list(db.scalars(select(spec.model).order_by(start_column.desc(), spec.model.id.desc()).limit(limit)).all())


def list_tasks(params: dict) -> dict:
    role, own_account_id = _scope(params)
    requested_account = params.get("account")
    status_filter = str(params.get("status") or "").strip().lower()
    kind_filter = str(params.get("kind") or "").strip().lower()
    query = str(params.get("query") or "").strip().lower()
    date_from = _date_filter(params.get("date_from"))
    date_to = _date_filter(params.get("date_to"), end=True)
    limit = min(500, max(1, _integer(params.get("limit", 100), name="task limit")))
    with read_session() as db:
        accounts = {row.id: row.username for row in db.scalars(select(Account)).all()}
        tasks = []
        for spec in SPECS:
            if kind_filter and spec.kind != kind_filter:
                continue
            for row in _load_rows(db, spec, limit=limit):
                task = _task(spec, row, accounts, role=role)
                if role == "customer" and task["account_id"] != own_account_id:
                    continue
                if role == "admin" and requested_account and task["account"] != requested_account:
                    continue
                if status_filter and task["status"] != status_filter:
                    continue
                task_started = _date_filter(task["started_at"])
                if date_from and (task_started is None or task_started < date_from):
                    continue
                if date_to and (task_started is None or task_started >= date_to):
                    continue
                if query and query not in " ".join(str(task.get(key) or "").lower() for key in (
                    "key", "kind", "label", "account", "status", "detail", "request_id", "error_reference"
                )):
                    continue
                tasks.append(task)
    tasks.sort(key=lambda item: item["started_at"] or "", reverse=True)
    return {"tasks": tasks[:limit], "total": len(tasks), "kinds": [spec.kind for spec in SPECS]}


def _row(db, kind: str, task_id: int) -> tuple[TaskSpec, Any]:
    spec = SPEC_BY_KIND.get(kind)
    if spec is None:
        raise ValidationError("Task type not found")
    row = db.get(spec.model, task_id)
    if row is None:
        raise ValidationError("Task not found")
    return spec, row


def owner_account_id(kind: object, task_id: object) -> int | None:
    task_id = _integer(task_id, name="task id")
    with read_session() as db:
        spec, row = _row(db, str(kind or ""), task_id)
        if spec.owner:
            return getattr(row, spec.owner, None)
        if spec.username:
            username = getattr(row, spec.username, None)
            return db.scalar(select(Account.id).where(Account.username == username)) if username else None
        return None


def _timeline(db, request_id: str | None) -> list[dict]:
    if not request_id:
        return []
    audit = db.scalar(select(AuditLog).where(AuditLog.request_id == request_id).order_by(AuditLog.id.desc()))
    if audit is None:
        return []
    rows = db.scalars(select(ActionStep).where(ActionStep.audit_id == audit.id).order_by(ActionStep.id)).all()
    return [{
        "name": row.name,
        "started_at": _iso(row.started_at),
        "duration_ms": row.duration_ms,
        "result": row.result,
        "exit_code": row.exit_code,
        "stdout": row.stdout_tail,
        "stderr": row.stderr_tail,
    } for row in rows]


def get_task(params: dict) -> dict:
    role, own_account_id = _scope(params)
    task_id = _integer(params.get("task_id"), name="task id")
    kind = str(params.get("kind") or "")
    with read_session() as db:
        accounts = {row.id: row.username for row in db.scalars(select(Account)).all()}
        spec, row = _row(db, kind, task_id)
        task = _task(spec, row, accounts, role=role)
        if role == "customer" and task["account_id"] != own_account_id:
            raise ValidationError("Task not found")
        task["timeline"] = _timeline(db, task["request_id"])
        return task


def retry_task(params: dict) -> dict:
    kind = str(params.get("kind") or "")
    task_id = _integer(params.get("task_id"), name="task id")
    if kind != "snapshot":
        raise ValidationError("This task does not support retry")
    result = snapshot_jobs.retry_run({"id": task_id})
    return {"action": "retry", "task": result}


def cancel_task(params: dict) -> dict:
    kind = str(params.get("kind") or "")
    task_id = _integer(params.get("task_id"), name="task id")
    if kind == "snapshot":
        result = snapshot_jobs.cancel_run({"id": task_id})
    elif kind == "imap-migration":
        account_id = owner_account_id(kind, task_id)
        with read_session() as db:
            username = db.scalar(select(Account.username).where(Account.id == account_id))
        if not username:
            raise ValidationError("Task owner not found")
        result = imapsync.cancel_migration({"id": task_id, "username": username})
    elif kind == "stack":
        result = stack_manager.cancel({"job_id": task_id})
    else:
        raise ValidationError("This task does not support cancellation")
    return {"action": "cancel", "task": result}
