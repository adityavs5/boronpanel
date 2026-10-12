"""Staged inode-quota enforcement for hosting accounts."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, utcnow
from shared.validation import ValidationError
from daemon import sysops

DEFAULT_SOFT = 450_000
DEFAULT_HARD = 500_000
DEFAULT_WARNING_DAYS = 7


def validate_limits(soft, hard) -> tuple[int, int]:
    try:
        soft, hard = int(soft), int(hard)
    except (TypeError, ValueError):
        raise ValidationError("inode soft and hard limits must be whole numbers") from None
    if soft < 0 or hard < 0:
        raise ValidationError("inode limits cannot be negative")
    if (soft == 0) != (hard == 0):
        raise ValidationError("set both inode limits to zero for unlimited, or set both limits")
    if hard and soft > hard:
        raise ValidationError("inode hard limit must be greater than or equal to the soft limit")
    return soft, hard


def schedule(account: Account, soft: int, hard: int, *, warning_days: int = DEFAULT_WARNING_DAYS) -> None:
    soft, hard = validate_limits(soft, hard)
    account.inode_soft_limit = soft
    account.inode_hard_limit = hard
    account.inode_enforce_at = (
        utcnow() + dt.timedelta(days=max(1, int(warning_days))) if hard else None
    )


def enforcement_state(account: Account, now: dt.datetime | None = None) -> str:
    if not account.inode_hard_limit:
        return "unlimited"
    if account.inode_enforce_at is None:
        return "enforced"
    now = now or utcnow()
    due = account.inode_enforce_at
    if due.tzinfo is None:
        due = due.replace(tzinfo=dt.timezone.utc)
    return "warning" if due > now else "due"


def enforce_due(params: dict | None = None) -> dict:
    """Apply only limits whose warning window expired; safe to run repeatedly."""
    now = utcnow()
    with write_session() as session:
        rows = session.scalars(
            select(Account).where(
                Account.status.in_(["active", "suspended"]),
                Account.inode_hard_limit > 0,
                Account.inode_enforce_at.is_not(None),
                Account.inode_enforce_at <= now,
            ).order_by(Account.id)
        ).all()
        candidates = [
            (row.id, row.username, row.quota_soft_mb, row.quota_hard_mb,
             row.inode_soft_limit, row.inode_hard_limit)
            for row in rows
        ]
    applied, failed = [], []
    for account_id, username, block_soft, block_hard, inode_soft, inode_hard in candidates:
        try:
            sysops.set_quota(
                username, block_soft, block_hard,
                inode_soft=inode_soft, inode_hard=inode_hard,
            )
            with write_session() as session:
                row = session.get(Account, account_id)
                if row is not None and row.inode_soft_limit == inode_soft and row.inode_hard_limit == inode_hard:
                    row.inode_enforce_at = None
            applied.append(username)
        except Exception:
            failed.append(username)
    return {"applied": applied, "failed": failed, "checked": len(candidates)}
