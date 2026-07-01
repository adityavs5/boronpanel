"""Resource usage reporting (Phase 2 feature 5) -- read-only.

Definition of Done requires these numbers to match what `du`/`mysql` show
independently, so every figure here is computed straight from the same
real sources an operator would check by hand (`du -sb`/`--inodes`,
information_schema, `ps`) -- never estimated or derived from Forgehost's
own bookkeeping.

Snapshots are refreshed at most every SNAPSHOT_MAX_AGE_SECONDS (15 min):
either lazily, the first time `get_usage` sees a stale/missing snapshot
(so a freshly created account still gets a real number on first look), or
proactively via scripts/usage_snapshot.py on a system cron (so historical
trend data keeps accumulating even if nobody opens the usage page).
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, BandwidthDaily, DatabaseGrant, Domain, MailDomain, UsageSnapshot, utcnow

from daemon import mariadb, ols
from daemon.procutil import run

SNAPSHOT_MAX_AGE_SECONDS = 15 * 60
BANDWIDTH_HISTORY_DAYS = 90
SNAPSHOT_HISTORY_DAYS = 30


def _as_aware_utc(value: dt.datetime) -> dt.datetime:
    """SQLite has no native timezone-aware datetime type -- SQLAlchemy's
    DateTime(timezone=True) still round-trips a value through it as naive
    (confirmed live: subtracting utcnow() from a freshly-read
    UsageSnapshot.taken_at raised "can't subtract offset-naive and
    offset-aware datetimes"). Every timestamp this project ever stores is
    written via utcnow() (shared/models.py), so a naive value read back is
    always UTC -- this just re-attaches the tzinfo lost in the round trip."""
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value

# OLS's default combined-ish access log format:
#   IP - - [01/Jul/2026:06:35:50 +0000] "METHOD path HTTP/ver" status bytes "referer" "ua"
_ACCESS_LOG_RE = re.compile(
    r'^\S+ \S+ \S+ \[(\d{2})/(\w{3})/(\d{4}):\d{2}:\d{2}:\d{2} [+-]\d{4}\] "[^"]*" \d+ (\d+|-)'
)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
)}


def _account_home(username: str) -> str:
    return f"{settings.home_base}/{username}"


def _du_bytes(path: str) -> int:
    if not Path(path).exists():
        return 0
    result = run(["du", "-sb", path], timeout=120)
    if not result.ok:
        return 0
    return int(result.stdout.split()[0])


def _du_inodes(path: str) -> int:
    if not Path(path).exists():
        return 0
    result = run(["du", "-s", "--inodes", path], timeout=120)
    if not result.ok:
        return 0
    return int(result.stdout.split()[0])


def _process_count(username: str) -> int:
    # `ps -u <user>` exits non-zero when zero processes match -- that's not
    # a real error here, so count output lines rather than checking .ok.
    result = run(["ps", "--no-headers", "-u", username], timeout=15)
    return len([line for line in result.stdout.splitlines() if line.strip()])


def _db_names_for_account(account_id: int) -> list[str]:
    with write_session() as session:
        return list(session.scalars(select(DatabaseGrant.db_name).where(DatabaseGrant.account_id == account_id)).all())


def _mail_domains_for_account(account_id: int) -> list[str]:
    with write_session() as session:
        return list(session.scalars(select(MailDomain.domain).where(MailDomain.account_id == account_id)).all())


def _domains_for_account(account_id: int) -> list[str]:
    with write_session() as session:
        return list(session.scalars(select(Domain.domain).where(Domain.account_id == account_id)).all())


def compute_live_usage(account: Account) -> dict:
    home_dir = _account_home(account.username)
    disk_mail = sum(_du_bytes(f"{settings.mail_base}/{d}") for d in _mail_domains_for_account(account.id))
    return {
        "disk_home_bytes": _du_bytes(home_dir),
        "disk_mail_bytes": disk_mail,
        "disk_db_bytes": mariadb.database_size_bytes(_db_names_for_account(account.id)),
        "inode_count": _du_inodes(home_dir),
        "process_count": _process_count(account.username),
    }


def _all_access_log_files(home_dir: str, vhost_name: str) -> list[Path]:
    logs_dir = Path(home_dir) / "logs"
    if not logs_dir.exists():
        return []
    # Glob, not just the current file: OLS rotates access logs (rollingSize/
    # keepDays in vhost.conf.j2) with a suffix this project doesn't control
    # the exact format of -- matching every file that starts with this
    # vhost's access log name catches rotated ones too.
    return sorted(logs_dir.glob(f"{vhost_name}-access.log*"))


def _parse_access_log(path: Path) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    try:
        with path.open("r", errors="replace") as f:
            for line in f:
                m = _ACCESS_LOG_RE.match(line)
                if not m:
                    continue
                day, mon, year, byte_str = m.groups()
                if byte_str == "-":
                    continue
                month_num = _MONTHS.get(mon)
                if month_num is None:
                    continue
                totals[f"{year}-{month_num:02d}-{day}"] += int(byte_str)
    except OSError:
        pass
    return totals


def refresh_bandwidth(account: Account) -> None:
    home_dir = _account_home(account.username)
    aggregate: dict[str, int] = defaultdict(int)
    for domain_name in _domains_for_account(account.id):
        vhost_name = ols._vhost_name(domain_name)
        for log_file in _all_access_log_files(home_dir, vhost_name):
            for date_key, byte_count in _parse_access_log(log_file).items():
                aggregate[date_key] += byte_count

    if not aggregate:
        return

    with write_session() as session:
        for date_key, byte_count in aggregate.items():
            existing = session.scalar(
                select(BandwidthDaily).where(BandwidthDaily.account_id == account.id, BandwidthDaily.date == date_key)
            )
            if existing is not None:
                existing.bytes_served = byte_count
            else:
                session.add(BandwidthDaily(account_id=account.id, date=date_key, bytes_served=byte_count))


def _snapshot_to_dict(snap: UsageSnapshot) -> dict:
    return {
        "taken_at": snap.taken_at.isoformat() if snap.taken_at else None,
        "disk_home_bytes": snap.disk_home_bytes,
        "disk_mail_bytes": snap.disk_mail_bytes,
        "disk_db_bytes": snap.disk_db_bytes,
        "disk_total_bytes": snap.disk_home_bytes + snap.disk_mail_bytes + snap.disk_db_bytes,
        "inode_count": snap.inode_count,
        "process_count": snap.process_count,
    }


def refresh_snapshot(account: Account) -> dict:
    live = compute_live_usage(account)
    refresh_bandwidth(account)
    with write_session() as session:
        snap = UsageSnapshot(account_id=account.id, **live)
        session.add(snap)
        session.flush()
        return _snapshot_to_dict(snap)


def get_usage(account: Account, force_refresh: bool = False) -> dict:
    with write_session() as session:
        latest = session.scalar(
            select(UsageSnapshot)
            .where(UsageSnapshot.account_id == account.id)
            .order_by(UsageSnapshot.taken_at.desc())
        )
        is_stale = latest is None or (utcnow() - _as_aware_utc(latest.taken_at)).total_seconds() > SNAPSHOT_MAX_AGE_SECONDS
        latest_dict = _snapshot_to_dict(latest) if latest is not None else None

    current = refresh_snapshot(account) if (force_refresh or is_stale) else latest_dict

    with write_session() as session:
        cutoff = utcnow() - dt.timedelta(days=SNAPSHOT_HISTORY_DAYS)
        history = session.scalars(
            select(UsageSnapshot)
            .where(UsageSnapshot.account_id == account.id, UsageSnapshot.taken_at >= cutoff)
            .order_by(UsageSnapshot.taken_at)
        ).all()
        bandwidth_cutoff = (utcnow() - dt.timedelta(days=BANDWIDTH_HISTORY_DAYS)).strftime("%Y-%m-%d")
        bandwidth_rows = session.scalars(
            select(BandwidthDaily)
            .where(BandwidthDaily.account_id == account.id, BandwidthDaily.date >= bandwidth_cutoff)
            .order_by(BandwidthDaily.date)
        ).all()

    return {
        "current": current,
        "quota_soft_mb": account.quota_soft_mb,
        "quota_hard_mb": account.quota_hard_mb,
        "history": [_snapshot_to_dict(s) for s in history],
        "bandwidth_daily": [{"date": b.date, "bytes_served": b.bytes_served} for b in bandwidth_rows],
        "bandwidth_month_to_date_bytes": sum(
            b.bytes_served for b in bandwidth_rows if b.date.startswith(utcnow().strftime("%Y-%m"))
        ),
    }


def refresh_all_accounts() -> int:
    """Entry point for scripts/usage_snapshot.py's periodic (system cron,
    every 15 min) background refresh -- keeps history accumulating even
    when nobody opens the usage page. Returns the number of accounts
    refreshed."""
    with write_session() as session:
        accounts = session.scalars(select(Account).where(Account.status.in_(["active", "suspended"]))).all()
        account_snapshots = list(accounts)

    count = 0
    for account in account_snapshots:
        refresh_snapshot(account)
        count += 1
    return count
