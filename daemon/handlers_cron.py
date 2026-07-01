"""Cron job CRUD RPC handlers (Phase 2 feature 2)."""
from __future__ import annotations

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account
from shared.validation import validate_username

from daemon import cron


def _get_active_account(username: str) -> Account:
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        if account.status not in ("active", "suspended"):
            raise RuntimeError(f"cannot manage cron jobs for an account in status '{account.status}'")
        return account


def list_cron_jobs(params: dict) -> dict:
    username = validate_username(params["username"])
    _get_active_account(username)
    return {"jobs": cron.list_jobs(username)}


def add_cron_job(params: dict) -> dict:
    username = validate_username(params["username"])
    _get_active_account(username)
    return cron.add_job(
        username,
        schedule=params["schedule"],
        command=params["command"],
        label=params.get("label", ""),
    )


def update_cron_job(params: dict) -> dict:
    username = validate_username(params["username"])
    _get_active_account(username)
    return cron.update_job(
        username,
        job_id=params["job_id"],
        schedule=params["schedule"],
        command=params["command"],
        label=params.get("label", ""),
    )


def delete_cron_job(params: dict) -> dict:
    username = validate_username(params["username"])
    _get_active_account(username)
    cron.delete_job(username, params["job_id"])
    return {"id": params["job_id"], "status": "deleted"}


def terminate_account_cron(account: Account) -> None:
    """TERMINATE_HOOKS entry: idempotent -- safe even if the account never
    had any cron jobs (delete_all_jobs treats "no crontab" as success)."""
    cron.delete_all_jobs(account.username)
