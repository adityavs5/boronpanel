"""Phase 8 feature 12: bulk multi-account operations.

Async (a bounded, single-worker executor), per-account progress in a
BulkActionJob row, and **stops at the first failure** (same safety rule the
namespace bulk-enable migration uses) -- `results` records each account
attempted, in order, up to and including the one that failed. Each action
reuses the existing, audited per-account handler so bulk behaves identically to
doing them one at a time.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, BulkActionJob, utcnow
from shared.validation import ValidationError, validate_username

from daemon import audit, handlers_account, notifications

logger = logging.getLogger("borond.bulkops")

# Single worker: bulk ops must be strictly sequential to honor "stop on first
# failure" (a parallel run could apply later accounts before an earlier one's
# failure is seen) -- same reasoning as nsisolation's migration executor.
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bulk-action")

ACTIONS = ("suspend", "unsuspend", "update_limits", "notify")


def _job_to_dict(job: BulkActionJob) -> dict:
    return {
        "id": job.id,
        "action": job.action,
        "status": job.status,
        "total": job.total,
        "completed_count": job.completed_count,
        "current_username": job.current_username,
        "results": job.results,
        "error": job.error,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def trigger_bulk_action(params: dict) -> dict:
    action = params.get("action")
    if action not in ACTIONS:
        raise ValidationError(f"action must be one of {ACTIONS}")
    usernames = params.get("usernames") or []
    if not isinstance(usernames, list) or not usernames:
        raise ValidationError("usernames must be a non-empty list")
    usernames = [validate_username(u) for u in usernames]
    action_params = params.get("action_params") or {}

    # Validate action_params up front so an obviously-bad request fails now, not
    # mid-run after some accounts were already changed.
    if action == "notify":
        subject = (action_params.get("subject") or "").strip()
        body = (action_params.get("body") or "").strip()
        if not subject or not body:
            raise ValidationError("notify requires a non-empty subject and body")
    if action == "update_limits":
        if not any(k in action_params for k in ("cpu_pct", "mem_mb", "io_mb", "pids_max")):
            raise ValidationError("update_limits requires at least one limit field")

    # Attribution for the account-events log: the API passes who triggered the
    # bulk action and from which IP, since the job itself runs detached.
    requested_by = str(params.get("requested_by") or "unknown")
    requested_ip = params.get("requested_ip") or None

    with write_session() as session:
        job = BulkActionJob(action=action, action_params=action_params, status="pending", total=len(usernames))
        session.add(job)
        session.flush()
        job_id = job.id

    _executor.submit(_run_job, job_id, action, usernames, action_params, requested_by, requested_ip)
    with write_session() as session:
        return _job_to_dict(session.get(BulkActionJob, job_id))


def _apply(action: str, username: str, action_params: dict, requested_by: str = "unknown", requested_ip: str | None = None) -> str:
    if action == "suspend":
        handlers_account.suspend_account({"username": username})
        audit.record_account_event("suspended", username, actor=requested_by, role="admin", ip=requested_ip, detail="bulk action")
        return "suspended"
    if action == "unsuspend":
        handlers_account.unsuspend_account({"username": username})
        audit.record_account_event("unsuspended", username, actor=requested_by, role="admin", ip=requested_ip, detail="bulk action")
        return "unsuspended"
    if action == "update_limits":
        handlers_account.set_limits({"username": username, **action_params})
        return "limits updated"
    if action == "notify":
        with write_session() as session:
            account = session.scalar(select(Account).where(Account.username == username))
            session.expunge(account) if account is not None else None
        sent = notifications.send_direct(account, action_params["subject"], action_params["body"])
        return "notified" if sent else "no contact email (skipped)"
    raise ValidationError(f"unknown action '{action}'")


def _run_job(job_id: int, action: str, usernames: list[str], action_params: dict, requested_by: str = "unknown", requested_ip: str | None = None) -> None:
    results: list[dict] = []
    with write_session() as session:
        session.get(BulkActionJob, job_id).status = "running"

    for username in usernames:
        with write_session() as session:
            job = session.get(BulkActionJob, job_id)
            job.current_username = username
        try:
            detail = _apply(action, username, action_params, requested_by, requested_ip)
            results.append({"username": username, "ok": True, "detail": detail})
            with write_session() as session:
                job = session.get(BulkActionJob, job_id)
                job.results = list(results)
                job.completed_count = len(results)
        except Exception as exc:  # noqa: BLE001 -- stop on first failure
            logger.exception("bulk action %s failed at %s", action, username)
            results.append({"username": username, "ok": False, "detail": str(exc)})
            with write_session() as session:
                job = session.get(BulkActionJob, job_id)
                job.results = list(results)
                job.completed_count = len(results)
                job.status = "failed"
                job.current_username = None
                job.error = f"stopped at '{username}': {exc}"
                job.completed_at = utcnow()
            return

    with write_session() as session:
        job = session.get(BulkActionJob, job_id)
        job.status = "completed"
        job.current_username = None
        job.completed_at = utcnow()


def get_bulk_action(params: dict) -> dict:
    job_id = int(params["job_id"])
    with write_session() as session:
        job = session.get(BulkActionJob, job_id)
        if job is None:
            raise RuntimeError(f"bulk action job {job_id} not found")
        return _job_to_dict(job)
