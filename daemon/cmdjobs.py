"""Phase 8 features 8/9: shared async runner for account-scoped commands.

Runs a validated argv AS THE ACCOUNT USER (`runuser -u <user> -- env HOME=...
<argv>`) in a target directory, captures stdout/stderr/exit code into a
CommandRun row, on a small bounded executor -- the same "commit the job row,
run it in the background, poll for status" shape WordPressJob/AppInstallJob use.
The command is never built by string concatenation (procutil.run enforces argv,
shell=False); password-bearing argv elements are redacted from the daemon log.
"""
from __future__ import annotations

import logging
import pwd
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, CommandRun, utcnow

from daemon.procutil import run

logger = logging.getLogger("forgehostd.cmdjobs")

_executor = ThreadPoolExecutor(max_workers=settings.command_run_concurrency, thread_name_prefix="cmdrun")
MAX_CAPTURE = 190_000  # keep well under the column ceiling


def _account(username: str) -> Account:
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        if account.status != "active":
            raise RuntimeError(f"cannot run commands for an account in status '{account.status}'")
        # return a detached-safe snapshot
        session.expunge(account)
        return account


def submit(
    username: str,
    kind: str,
    target: str,
    argv: list[str],
    command_display: str,
    *,
    redact: list[str] | None = None,
    revealed_secret: str | None = None,
    timeout: float | None = None,
) -> dict:
    """Create a CommandRun row and run `argv` as the account user in `target`.
    Returns the created job dict (status 'pending'). argv is the FULL command to
    run as the account user (already including runuser/env wrapping is NOT done
    here -- callers pass the bare tool argv; this wraps it)."""
    account = _account(username)
    pw = pwd.getpwnam(username)
    with write_session() as session:
        job = CommandRun(
            account_id=account.id, kind=kind, target=target,
            command_display=command_display[:1024], status="pending",
            revealed_secret=revealed_secret,
        )
        session.add(job)
        session.flush()
        job_id = job.id

    wrapped = ["runuser", "-u", username, "--", "env", f"HOME={pw.pw_dir}", *argv]
    _executor.submit(
        _run_job, job_id, wrapped, target, redact or [], timeout or settings.command_run_timeout_seconds
    )
    return _job_dict(_get(job_id), reveal=False)


def _run_job(job_id: int, argv: list[str], cwd: str, redact: list[str], timeout: float) -> None:
    with write_session() as session:
        job = session.get(CommandRun, job_id)
        job.status = "running"
    try:
        result = run(argv, cwd=cwd, timeout=timeout, redact=redact or None)
        stdout = result.stdout[:MAX_CAPTURE]
        stderr = result.stderr[:MAX_CAPTURE]
        with write_session() as session:
            job = session.get(CommandRun, job_id)
            job.exit_code = result.returncode
            job.stdout = stdout
            job.stderr = stderr
            job.status = "completed" if result.returncode == 0 else "failed"
            if result.returncode != 0 and not job.error:
                job.error = (stderr.strip() or stdout.strip() or "command exited non-zero")[:4000]
            job.completed_at = utcnow()
    except Exception as exc:  # noqa: BLE001
        logger.exception("command run %s failed", job_id)
        with write_session() as session:
            job = session.get(CommandRun, job_id)
            job.status = "failed"
            job.error = str(exc)[:4000]
            job.completed_at = utcnow()


def _get(job_id: int) -> CommandRun:
    with write_session() as session:
        job = session.get(CommandRun, job_id)
        if job is None:
            raise RuntimeError(f"command run {job_id} not found")
        session.expunge(job)
        return job


def _job_dict(job: CommandRun, reveal: bool) -> dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "target": job.target,
        "command": job.command_display,
        "status": job.status,
        "exit_code": job.exit_code,
        "stdout": job.stdout,
        "stderr": job.stderr,
        "error": job.error,
        "revealed_secret": job.revealed_secret if reveal else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def get_run(params: dict) -> dict:
    """Fetch a run's status. On the first observation of a completed run, the
    one-time revealed_secret (a reset password) is returned and then cleared,
    so it can never leak from the row on a later poll."""
    job_id = int(params["job_id"])
    username = params["username"]
    with write_session() as session:
        job = session.get(CommandRun, job_id)
        if job is None:
            raise RuntimeError(f"command run {job_id} not found")
        account = session.get(Account, job.account_id)
        if account is None or account.username != username:
            raise RuntimeError("command run does not belong to this account")
        reveal = job.status in ("completed", "failed") and job.revealed_secret is not None
        result = _job_dict(job, reveal=reveal)
        if reveal:
            job.revealed_secret = None  # one-time reveal
        return result


def list_runs(params: dict, kind: str) -> dict:
    username = params["username"]
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        rows = session.scalars(
            select(CommandRun)
            .where(CommandRun.account_id == account.id, CommandRun.kind == kind)
            .order_by(CommandRun.id.desc())
            .limit(50)
        ).all()
        return {"runs": [_job_dict(r, reveal=False) for r in rows]}
