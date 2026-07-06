"""Phase 7b feature 5: automated alerts at 80/90/100% of an account's
resource limits (disk, bandwidth, databases, email accounts, subdomains).

Disk reuses Account.quota_hard_mb (Phase a, OS-enforced) as its 100%
reference rather than a duplicate limit field here -- every other resource
has no existing quota column, so AccountResourceLimits holds those.
Alerting piggybacks on data every other feature already collects/computes
(daemon/usage.py's cached usage snapshot, DatabaseGrant/MailUser/Domain
row counts) -- no new collection, only new threshold logic + history.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from shared.db import write_session
from shared.models import (
    Account,
    AccountResourceLimits,
    DatabaseGrant,
    Domain,
    MailDomain,
    MailUser,
    UsageAlert,
    utcnow,
)
from shared.validation import validate_resource_limit, validate_username

from daemon import events, handlers_account, usage

logger = logging.getLogger("forgehostd.usage_alerts")

THRESHOLDS = (100, 90, 80)
RESOURCES = ("disk", "bandwidth", "databases", "email_accounts", "subdomains")


class UsageAlertError(Exception):
    pass


def _get_limits_row(session, account_id: int) -> AccountResourceLimits:
    row = session.scalar(select(AccountResourceLimits).where(AccountResourceLimits.account_id == account_id))
    if row is None:
        row = AccountResourceLimits(account_id=account_id)
        session.add(row)
        session.flush()
    return row


def _limits_to_dict(row: AccountResourceLimits) -> dict:
    return {
        "bandwidth_limit_mb": row.bandwidth_limit_mb,
        "database_limit": row.database_limit,
        "email_account_limit": row.email_account_limit,
        "subdomain_limit": row.subdomain_limit,
        "auto_suspend_at_100": row.auto_suspend_at_100,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_limits(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        return _limits_to_dict(_get_limits_row(session, account.id))


def set_limits(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        row = _get_limits_row(session, account.id)
        if "bandwidth_limit_mb" in params:
            row.bandwidth_limit_mb = validate_resource_limit(params["bandwidth_limit_mb"], "bandwidth_limit_mb")
        if "database_limit" in params:
            row.database_limit = validate_resource_limit(params["database_limit"], "database_limit")
        if "email_account_limit" in params:
            row.email_account_limit = validate_resource_limit(params["email_account_limit"], "email_account_limit")
        if "subdomain_limit" in params:
            row.subdomain_limit = validate_resource_limit(params["subdomain_limit"], "subdomain_limit")
        if "auto_suspend_at_100" in params:
            row.auto_suspend_at_100 = bool(params["auto_suspend_at_100"])
        session.flush()
        return _limits_to_dict(row)


def _alert_to_dict(a: UsageAlert) -> dict:
    return {
        "id": a.id,
        "resource": a.resource,
        "threshold_pct": a.threshold_pct,
        "triggered_at": a.triggered_at.isoformat() if a.triggered_at else None,
        "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
        "acknowledged": a.acknowledged,
    }


def get_alerts(params: dict) -> dict:
    username = validate_username(params["username"])
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == username))
        if account is None:
            raise RuntimeError(f"account '{username}' not found")
        rows = session.scalars(
            select(UsageAlert).where(UsageAlert.account_id == account.id).order_by(UsageAlert.triggered_at.desc())
        ).all()
        active = [r for r in rows if r.resolved_at is None]
        return {
            "alerts": [_alert_to_dict(r) for r in rows],
            "active": [_alert_to_dict(r) for r in active],
        }


def _database_count(account_id: int) -> int:
    with write_session() as session:
        return len(session.scalars(select(DatabaseGrant.id).where(DatabaseGrant.account_id == account_id)).all())


def _email_account_count(account_id: int) -> int:
    with write_session() as session:
        domain_ids = session.scalars(select(MailDomain.id).where(MailDomain.account_id == account_id)).all()
        if not domain_ids:
            return 0
        return len(session.scalars(select(MailUser.id).where(MailUser.mail_domain_id.in_(domain_ids))).all())


def _subdomain_count(account_id: int) -> int:
    with write_session() as session:
        return len(
            session.scalars(
                select(Domain.id).where(Domain.account_id == account_id, Domain.kind == "subdomain")
            ).all()
        )


def _resource_usage(account: Account, limits: AccountResourceLimits) -> dict[str, tuple[float, float | None]]:
    """Returns {resource: (current, limit_or_None)} -- limit None means
    "not tracked for this resource", the caller skips alerting on it."""
    usage_data = usage.get_usage(account)
    disk_current = (usage_data["current"] or {}).get("disk_total_bytes", 0) or 0
    disk_limit_bytes = account.quota_hard_mb * 1024 * 1024 if account.quota_hard_mb else None
    bandwidth_current = usage_data.get("bandwidth_month_to_date_bytes", 0) or 0
    bandwidth_limit_bytes = limits.bandwidth_limit_mb * 1024 * 1024 if limits.bandwidth_limit_mb else None

    return {
        "disk": (disk_current, disk_limit_bytes),
        "bandwidth": (bandwidth_current, bandwidth_limit_bytes),
        "databases": (_database_count(account.id), limits.database_limit),
        "email_accounts": (_email_account_count(account.id), limits.email_account_limit),
        "subdomains": (_subdomain_count(account.id), limits.subdomain_limit),
    }


def _highest_crossed(pct: float) -> int | None:
    for threshold in THRESHOLDS:
        if pct >= threshold:
            return threshold
    return None


def check_usage_alerts(account: Account) -> list[dict]:
    """Runs the 80/90/100% check for every tracked resource on one
    account, creating/resolving UsageAlert rows and firing
    "usage.limit.reached" (feature 3 email + feature 4 webhook) on each
    NEW escalation -- never re-fires for a resource still sitting at the
    same or a lower already-alerted threshold, and never fires when
    usage merely fluctuates within the same >=80% band (goal: alert once
    per threshold crossing, not on every check). Returns the list of
    newly created alerts (empty if nothing changed)."""
    with write_session() as session:
        limits = _get_limits_row(session, account.id)
        limits_snapshot = AccountResourceLimits(
            bandwidth_limit_mb=limits.bandwidth_limit_mb,
            database_limit=limits.database_limit,
            email_account_limit=limits.email_account_limit,
            subdomain_limit=limits.subdomain_limit,
            auto_suspend_at_100=limits.auto_suspend_at_100,
        )

    resource_usage = _resource_usage(account, limits_snapshot)
    new_alerts: list[dict] = []
    should_auto_suspend = False

    for resource, (current, limit) in resource_usage.items():
        if not limit:
            continue
        pct = (current / limit) * 100
        crossed = _highest_crossed(pct)

        with write_session() as session:
            existing = session.scalar(
                select(UsageAlert)
                .where(UsageAlert.account_id == account.id, UsageAlert.resource == resource, UsageAlert.resolved_at.is_(None))
                .order_by(UsageAlert.triggered_at.desc())
            )
            if crossed is None:
                if existing is not None:
                    existing.resolved_at = utcnow()
                continue
            if existing is not None and crossed <= existing.threshold_pct:
                continue  # already alerted at this or a higher level, still active
            if existing is not None:
                existing.resolved_at = utcnow()
            alert = UsageAlert(account_id=account.id, resource=resource, threshold_pct=crossed)
            session.add(alert)
            session.flush()
            new_alerts.append(_alert_to_dict(alert))

        events.emit("usage.limit.reached", account, resource=resource, threshold_pct=crossed)
        if crossed == 100 and limits_snapshot.auto_suspend_at_100:
            should_auto_suspend = True

    if should_auto_suspend:
        try:
            handlers_account.suspend_account({"username": account.username})
        except Exception:  # noqa: BLE001 - alerting itself must not fail if auto-suspend can't apply
            logger.exception("auto-suspend at 100%% failed for account '%s'", account.username)

    return new_alerts


def check_all_accounts() -> int:
    """Entry point for scripts/usage_alert_check.py (system cron). Returns
    the number of accounts that got at least one new alert this pass."""
    with write_session() as session:
        accounts = session.scalars(select(Account).where(Account.status.in_(["active", "suspended"]))).all()
        account_snapshots = list(accounts)

    triggered = 0
    for account in account_snapshots:
        try:
            if check_usage_alerts(account):
                triggered += 1
        except Exception:  # noqa: BLE001 - one account's failure must not skip the rest
            logger.exception("usage alert check failed for account '%s'", account.username)
    return triggered
