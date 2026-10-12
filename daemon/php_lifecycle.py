"""PHP release lifecycle policy and gradual per-account session migration."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, Domain, PhpLifecycleSettings, utcnow
from shared.validation import ValidationError
from daemon import ols, sysops


# Security-support end dates published by php.net.  Keeping this small table
# in code makes the UI deterministic/offline and gives upgrades an explicit
# review point when a new PHP branch is added.
PHP_EOL_DATES = {
    "8.1": dt.date(2025, 12, 31),
    "8.2": dt.date(2026, 12, 31),
    "8.3": dt.date(2027, 12, 31),
    "8.4": dt.date(2028, 12, 31),
    "8.5": dt.date(2029, 12, 31),
}
DEFAULT_HIDE_EOL_AFTER = "2026-12-31"


def _date(value: str | None, *, field: str) -> dt.date | None:
    if value in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise ValidationError(f"{field} must be a date in YYYY-MM-DD format") from None


def lifecycle(version: str, today: dt.date | None = None) -> dict:
    today = today or utcnow().date()
    eol = PHP_EOL_DATES.get(version)
    return {
        "version": version,
        "eol_date": eol.isoformat() if eol else None,
        "status": "eol" if eol is not None and today > eol else "supported",
        "is_eol": bool(eol is not None and today > eol),
    }


def get_policy(params: dict | None = None) -> dict:
    with write_session() as session:
        row = session.get(PhpLifecycleSettings, 1)
        hide_after = row.hide_eol_for_new_domains_after if row else DEFAULT_HIDE_EOL_AFTER
    today = utcnow().date()
    threshold = _date(hide_after, field="hide_eol_for_new_domains_after")
    hide_now = threshold is not None and today >= threshold
    return {
        "hide_eol_for_new_domains_after": hide_after,
        "hide_eol_for_new_domains_now": hide_now,
        "versions": [lifecycle(version, today) for version in sorted(PHP_EOL_DATES)],
    }


def update_policy(params: dict) -> dict:
    value = params.get("hide_eol_for_new_domains_after")
    parsed = _date(value, field="hide_eol_for_new_domains_after")
    rendered = parsed.isoformat() if parsed else None
    with write_session() as session:
        row = session.get(PhpLifecycleSettings, 1)
        if row is None:
            row = PhpLifecycleSettings(id=1)
            session.add(row)
        row.hide_eol_for_new_domains_after = rendered
    return get_policy({})


def versions_for_new_domain(versions: list[str] | tuple[str, ...]) -> list[str]:
    policy = get_policy({})
    if not policy["hide_eol_for_new_domains_now"]:
        return list(versions)
    states = {item["version"]: item for item in policy["versions"]}
    return [version for version in versions if not states.get(version, {}).get("is_eol")]


def version_usage(version: str) -> dict:
    with write_session() as session:
        accounts = session.scalars(
            select(Account).where(
                Account.php_version == version,
                Account.status.in_(["active", "suspended"]),
            ).order_by(Account.username)
        ).all()
        domains = session.execute(
            select(Domain.domain, Account.username).join(Account, Account.id == Domain.account_id).where(
                Domain.php_version == version,
                Account.status.in_(["active", "suspended"]),
            ).order_by(Account.username, Domain.domain)
        ).all()
    return {
        "accounts": [{"username": account.username, "primary_domain": account.primary_domain} for account in accounts],
        "domains": [{"domain": domain, "username": username} for domain, username in domains],
        "count": len(accounts) + len(domains),
    }


def session_migration_preview(params: dict | None = None) -> dict:
    with write_session() as session:
        accounts = session.scalars(
            select(Account).where(
                Account.status.in_(["active", "suspended"]),
                Account.php_private_sessions == False,  # noqa: E712
            ).order_by(Account.username)
        ).all()
        already = session.scalar(select(Account.id).where(Account.php_private_sessions == True))  # noqa: E712
    return {
        "affected_count": len(accounts),
        "accounts": [account.username for account in accounts],
        "already_migrated": already is not None,
        "warning": "Migrating restarts each affected account's PHP workers, so active PHP sessions will end.",
    }


def migrate_sessions(params: dict) -> dict:
    if not bool(params.get("confirm")):
        raise ValidationError("PHP session migration requires confirm=true")
    preview = session_migration_preview({})
    usernames = preview["accounts"]
    if not usernames:
        return {**preview, "status": "unchanged", "migrated": []}
    for username in usernames:
        sysops.ensure_php_session_dir(username)
    with write_session() as session:
        rows = session.scalars(select(Account).where(Account.username.in_(usernames))).all()
        for row in rows:
            row.php_private_sessions = True
    try:
        ols.refresh_all_vhosts()
    except Exception:
        with write_session() as session:
            rows = session.scalars(select(Account).where(Account.username.in_(usernames))).all()
            for row in rows:
                row.php_private_sessions = False
        raise
    for username in usernames:
        sysops.recycle_php_workers(username)
    return {
        "status": "completed",
        "affected_count": len(usernames),
        "migrated": usernames,
        "warning": preview["warning"],
    }
