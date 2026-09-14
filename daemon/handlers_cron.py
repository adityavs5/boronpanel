"""Cron job CRUD RPC handlers (Phase 2 feature 2)."""
from __future__ import annotations

from functools import wraps
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


def _mutation(function):
    @wraps(function)
    def wrapped(params):
        username = validate_username(params['username'])
        account = _get_active_account(username)
        from daemon.snapshot_jobs import lock
        from shared.validation import ValidationError
        try:
            with lock(f'account-{account.id}', blocking=False):
                return function(params)
        except BlockingIOError:
            raise ValidationError('An account backup or restore is in progress. Try again shortly.') from None
    return wrapped


def list_cron_jobs(params: dict) -> dict:
    username = validate_username(params["username"])
    _get_active_account(username)
    return {"jobs": cron.list_jobs(username)}


@_mutation
def add_cron_job(params: dict) -> dict:
    username = validate_username(params["username"])
    _get_active_account(username)
    return cron.add_job(
        username,
        schedule=params["schedule"],
        command=params["command"],
        label=params.get("label", ""),
    )


@_mutation
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


@_mutation
def delete_cron_job(params: dict) -> dict:
    username = validate_username(params["username"])
    _get_active_account(username)
    cron.delete_job(username, params["job_id"])
    return {"id": params["job_id"], "status": "deleted"}


def get_cron_mailto(params: dict) -> dict:
    username = validate_username(params["username"])
    _get_active_account(username)
    return {"mailto": cron.get_mailto(username)}


@_mutation
def set_cron_mailto(params: dict) -> dict:
    username = validate_username(params["username"])
    _get_active_account(username)
    mailto = cron.set_mailto(username, params.get("mailto", ""))
    return {"mailto": mailto}


def terminate_account_cron(account: Account) -> None:
    """TERMINATE_HOOKS entry: idempotent -- safe even if the account never
    had any cron jobs (delete_all_jobs treats "no crontab" as success)."""
    cron.delete_all_jobs(account.username)
