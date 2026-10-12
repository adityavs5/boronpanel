"""Durable task wrappers for control-plane operations that can take seconds."""
from __future__ import annotations

import json
import logging

from sqlalchemy import select

from daemon import (
    audit,
    events,
    handlers_account,
    handlers_domain,
    identity_admin,
    ipmanager,
    jobcredentials,
    plans,
)
from shared.db import write_session
from shared.failures import describe_failure
from shared.models import Account, ControlTask, utcnow
from shared.request_context import ContextThreadPoolExecutor, ensure_error_reference
from shared.validation import ValidationError, validate_domain, validate_username

logger = logging.getLogger("borond.control_tasks")
_executor = ContextThreadPoolExecutor(max_workers=1, thread_name_prefix="control-task")

OPERATIONS = frozenset({"account.create", "account.rename", "account.terminate", "domain.add"})


def _safe_task(row: ControlTask) -> dict:
    return {
        "id": row.id,
        "operation": row.operation,
        "account_id": row.account_id,
        "username": row.username,
        "status": row.status,
        "progress": row.progress_pct,
        "progress_message": row.progress_message,
        "result": row.result or {},
        "request_id": row.request_id,
        "error_reference": row.error_reference,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def _principal(params: dict) -> tuple[str, str, str | None]:
    actor = str(params.pop("_authenticated_username", "") or "")
    role = str(params.pop("_authenticated_role", "") or "")
    source_ip = params.pop("_source_ip", None)
    params.pop("_authenticated_account_id", None)
    if role not in {"admin", "customer"} or not actor:
        raise ValidationError("Authenticated panel session required")
    return actor, role, source_ip


def _start(operation: str, params: dict, *, account_id: int | None = None) -> dict:
    if operation not in OPERATIONS:
        raise ValidationError("Unsupported control task")
    actor, role, source_ip = _principal(params)
    password = params.pop("password", None)
    secret = jobcredentials.seal(json.dumps({"password": password})) if password is not None else None
    username = str(params.get("username") or "") or None
    with write_session() as db:
        row = ControlTask(
            operation=operation,
            account_id=account_id,
            username=username,
            safe_params=dict(params),
            secret_params_enc=secret,
            initiated_by=actor,
            initiated_role=role,
            source_ip=source_ip,
        )
        db.add(row)
        db.flush()
        task_id = row.id
        result = _safe_task(row)
    _executor.submit(_run, task_id)
    return result


def start_account_create(params: dict) -> dict:
    params = dict(params)
    params["username"] = validate_username(params.get("username"))
    if params.get("primary_domain"):
        params["primary_domain"] = validate_domain(params["primary_domain"])
    return _start("account.create", params)


def _existing_account(username: object) -> tuple[str, int]:
    username = validate_username(username)
    with write_session() as db:
        account = db.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise ValidationError("Account not found")
        return username, account.id


def start_domain_add(params: dict) -> dict:
    params = dict(params)
    username, account_id = _existing_account(params.get("username"))
    params["username"] = username
    params["domain"] = validate_domain(params.get("domain"))
    if params.get("parent_domain"):
        params["parent_domain"] = validate_domain(params["parent_domain"])
    return _start("domain.add", params, account_id=account_id)


def start_account_rename(params: dict) -> dict:
    params = dict(params)
    username, account_id = _existing_account(params.get("username"))
    params["username"] = username
    params["new_username"] = validate_username(params.get("new_username"))
    return _start("account.rename", params, account_id=account_id)


def start_account_terminate(params: dict) -> dict:
    params = dict(params)
    username, account_id = _existing_account(params.get("username"))
    params["username"] = username
    return _start("account.terminate", params, account_id=account_id)


def _set(task_id: int, **values) -> None:
    with write_session() as db:
        row = db.get(ControlTask, task_id)
        if row is not None:
            for key, value in values.items():
                setattr(row, key, value)


def _account_snapshot(account_id: int | None):
    if account_id is None:
        return None
    with write_session() as db:
        account = db.get(Account, account_id)
        if account is not None:
            db.expunge(account)
        return account


def _execute(row: ControlTask, params: dict) -> tuple[dict, str | None, int | None, str]:
    operation = row.operation
    username = str(params.get("username") or row.username or "")
    if operation == "account.create":
        plan_id = params.pop("plan_id", None)
        ip_selection = params.pop("ip_selection", "automatic")
        server_ip_id = params.pop("server_ip_id", None)
        _set(row.id, progress_pct=10, progress_message=f"Creating account {username}")
        result = handlers_account.create_account(params)
        one_time_secret = result.pop("initial_password", None)
        account_id = int(result["id"])
        warnings = []
        if plan_id is not None:
            _set(row.id, progress_pct=60, progress_message="Applying hosting plan")
            try:
                applied = plans.apply_plan({"username": result["username"], "plan_id": plan_id})
                result.update(applied)
            except Exception:  # noqa: BLE001 - preserve the existing partial-success contract
                warnings.append("The account was created, but its plan needs review.")
        _set(row.id, progress_pct=80, progress_message="Assigning server address")
        try:
            allocation_params = {"username": result["username"], "selection": ip_selection}
            if server_ip_id is not None:
                allocation_params["server_ip_id"] = server_ip_id
            result["server_ip"] = ipmanager.assign_for_new_account(allocation_params)
        except Exception:  # noqa: BLE001 - preserve the existing partial-success contract
            warnings.append("The account was created, but its server address needs review.")
        if warnings:
            result["warnings"] = warnings
        audit.record_account_event("created", result["username"], actor=row.initiated_by,
                                   role=row.initiated_role, ip=row.source_ip, detail="control task")
        return result, one_time_secret, account_id, result["username"]
    if operation == "domain.add":
        _set(row.id, progress_pct=20, progress_message=f"Adding domain {params['domain']}")
        result = handlers_domain.add_domain(params)
        return result, None, row.account_id, username
    if operation == "account.rename":
        new_username = params["new_username"]
        _set(row.id, progress_pct=15, progress_message=f"Renaming {username} to {new_username}; this can take up to 30 seconds")
        result = identity_admin.rename_account(params)
        return result, None, row.account_id, new_username
    if operation == "account.terminate":
        _set(row.id, progress_pct=15, progress_message=f"Terminating {username}; this can take up to 30 seconds")
        result = handlers_account.terminate_account(params)
        audit.record_account_event("terminated", username, actor=row.initiated_by,
                                   role=row.initiated_role, ip=row.source_ip, detail="control task")
        return result, None, row.account_id, username
    raise ValidationError("Unsupported control task")


def _run(task_id: int) -> None:
    with write_session() as db:
        row = db.get(ControlTask, task_id)
        if row is None or row.status != "queued":
            return
        row.status = "running"
        row.started_at = utcnow()
        row.progress_pct = max(1, row.progress_pct)
        row.progress_message = "Starting"
        safe_params = dict(row.safe_params or {})
        if row.secret_params_enc:
            secrets = json.loads(jobcredentials.reveal(row.secret_params_enc) or "{}")
            if secrets.get("password") is not None:
                safe_params["password"] = secrets["password"]
        snapshot = {
            "operation": row.operation, "username": row.username,
            "initiated_by": row.initiated_by, "initiated_role": row.initiated_role,
            "source_ip": row.source_ip, "account_id": row.account_id,
        }
    trace, token = audit.begin_action_trace(snapshot["operation"], safe_params, enabled=True)
    try:
        with write_session() as db:
            row = db.get(ControlTask, task_id)
            result, one_time_secret, account_id, username = _execute(row, safe_params)
        safe_result = dict(result or {})
        safe_result.pop("initial_password", None)
        _set(
            task_id,
            status="completed",
            progress_pct=100,
            progress_message="Completed",
            result=safe_result,
            one_time_secret_enc=jobcredentials.seal(one_time_secret),
            secret_params_enc=None,
            account_id=account_id,
            username=username,
            completed_at=utcnow(),
        )
        audit.record(
            snapshot["initiated_by"], snapshot["initiated_role"], snapshot["operation"],
            username, safe_params, "ok", source_ip=snapshot["source_ip"], trace=trace,
        )
    except Exception as exc:  # noqa: BLE001 - convert every worker failure to a safe durable result
        diagnostic = describe_failure(snapshot["operation"], exc)
        ensure_error_reference(diagnostic["reference"])
        logger.error("control task failed: %s", json.dumps(diagnostic, separators=(",", ":")))
        _set(
            task_id,
            status="failed",
            progress_pct=100,
            progress_message="Operation failed",
            secret_params_enc=None,
            error_reference=diagnostic["reference"],
            completed_at=utcnow(),
        )
        audit.record(
            snapshot["initiated_by"], snapshot["initiated_role"], snapshot["operation"],
            snapshot["username"], safe_params, "failed",
            f"{diagnostic['error_type']} (reference: {diagnostic['reference']})",
            source_ip=snapshot["source_ip"], trace=trace,
        )
        events.emit(
            "task.failed", _account_snapshot(snapshot["account_id"]),
            task_id=task_id, kind=snapshot["operation"], reference=diagnostic["reference"],
        )
    finally:
        audit.end_action_trace(token)


def get_task(params: dict) -> dict:
    task_id = int(params["task_id"])
    with write_session() as db:
        row = db.get(ControlTask, task_id)
        if row is None:
            raise ValidationError("Task not found")
        return _safe_task(row)


def reveal_result(params: dict) -> dict:
    task_id = int(params["task_id"])
    with write_session() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = db.get(ControlTask, task_id)
        if row is None or row.status != "completed":
            raise ValidationError("Completed task not found")
        secret = jobcredentials.reveal(row.one_time_secret_enc)
        row.one_time_secret_enc = None
        result = dict(row.result or {})
        if secret is not None:
            result["initial_password"] = secret
        return result


def recover_pending() -> dict:
    """Resume queued work; mark indeterminate in-flight work failed safely."""
    queued = []
    interrupted = []
    with write_session() as db:
        for row in db.scalars(select(ControlTask).where(ControlTask.status.in_(("queued", "running")))).all():
            if row.status == "queued":
                queued.append(row.id)
            else:
                diagnostic = describe_failure(row.operation, RuntimeError("task interrupted by restart"))
                row.status = "failed"
                row.progress_pct = 100
                row.progress_message = "Interrupted by a panel restart; review the target before retrying"
                row.error_reference = diagnostic["reference"]
                row.secret_params_enc = None
                row.completed_at = utcnow()
                interrupted.append(row.id)
    for task_id in queued:
        _executor.submit(_run, task_id)
    return {"resumed": queued, "interrupted": interrupted}
