"""Usage-reporting RPC handler (Phase 2 feature 5)."""
from __future__ import annotations

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account
from shared.validation import validate_username

from daemon import usage


def get_account_usage(params: dict) -> dict:
    username = validate_username(params["username"])
    force_refresh = bool(params.get("force_refresh", False))
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        account_snapshot = account
    return usage.get_usage(account_snapshot, force_refresh=force_refresh)
