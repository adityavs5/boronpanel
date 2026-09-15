"""Resource usage reporting (Phase 2 feature 5) -- read-only.

Definition of Done requires these numbers to match what `du`/`mysql` show
independently, so every figure here is computed straight from the same
real sources an operator would check by hand (`du -sb`/`--inodes`,
information_schema, `ps`) -- never estimated or derived from Boron's
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

from sqlalchemy import func, select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, AccountResourceLimits, BandwidthDaily, BandwidthDailyDomain, DatabaseGrant, Domain, FtpAccount, MailDomain, MailUser, UsageSnapshot, utcnow

from daemon import mariadb, ols
from daemon.procutil import run

SNAPSHOT_MAX_AGE_SECONDS = 15 * 60
BANDWIDTH_HISTORY_DAYS = 90
SNAPSHOT_HISTORY_DAYS = 30
CGROUP_ROOT = Path("/sys/fs/cgroup/boron.slice")


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
    # Phase 7b feature 2: bandwidth graphs need "top 5 domains by bandwidth"
    # -- per_domain keeps the identical (date -> bytes) breakdown the
    # account-level `aggregate` above already computes, just one level
    # finer, from the exact same access-log parse pass (no second log read).
    per_domain: dict[tuple[str, str], int] = defaultdict(int)
    for domain_name in _domains_for_account(account.id):
        vhost_name = ols._vhost_name(domain_name)
        for log_file in _all_access_log_files(home_dir, vhost_name):
            for date_key, byte_count in _parse_access_log(log_file).items():
                aggregate[date_key] += byte_count
                per_domain[(domain_name, date_key)] += byte_count

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

        for (domain_name, date_key), byte_count in per_domain.items():
            existing_domain = session.scalar(
                select(BandwidthDailyDomain).where(
                    BandwidthDailyDomain.account_id == account.id,
                    BandwidthDailyDomain.domain == domain_name,
                    BandwidthDailyDomain.date == date_key,
                )
            )
            if existing_domain is not None:
                existing_domain.bytes_served = byte_count
            else:
                session.add(
                    BandwidthDailyDomain(account_id=account.id, domain=domain_name, date=date_key, bytes_served=byte_count)
                )


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


def _integer_file(path: Path) -> int | None:
    try:
        value = path.read_text().strip()
        return int(value) if value and value != "max" else None
    except (OSError, ValueError):
        return None


def _cgroup_counters(username: str) -> dict:
    """Cheap cumulative counters for a dashboard client to turn into rates."""
    root = CGROUP_ROOT / f"boron-{username}.slice"
    cpu_usage_usec = None
    try:
        cpu = dict(line.split(None, 1) for line in (root / "cpu.stat").read_text().splitlines() if " " in line)
        cpu_usage_usec = int(cpu.get("usage_usec", 0))
    except (OSError, ValueError):
        pass
    totals = {"read_bytes": 0, "write_bytes": 0, "read_ops": 0, "write_ops": 0}
    try:
        for line in (root / "io.stat").read_text().splitlines():
            values = dict(token.split("=", 1) for token in line.split()[1:] if "=" in token)
            totals["read_bytes"] += int(values.get("rbytes", 0))
            totals["write_bytes"] += int(values.get("wbytes", 0))
            totals["read_ops"] += int(values.get("rios", 0))
            totals["write_ops"] += int(values.get("wios", 0))
    except (OSError, ValueError):
        totals = {key: None for key in totals}
    return {
        "sampled_at": utcnow().isoformat(),
        "cpu_usage_usec": cpu_usage_usec,
        "memory_current_bytes": _integer_file(root / "memory.current"),
        "pids_current": _integer_file(root / "pids.current"),
        **totals,
    }


def _resource_summary(account: Account) -> dict:
    with write_session() as session:
        limits = session.scalar(select(AccountResourceLimits).where(AccountResourceLimits.account_id == account.id))
        database_count = session.scalar(select(func.count()).select_from(DatabaseGrant).where(DatabaseGrant.account_id == account.id)) or 0
        domain_count = session.scalar(select(func.count()).select_from(Domain).where(Domain.account_id == account.id, Domain.kind != "subdomain")) or 0
        subdomain_count = session.scalar(select(func.count()).select_from(Domain).where(Domain.account_id == account.id, Domain.kind == "subdomain")) or 0
        ftp_count = session.scalar(select(func.count()).select_from(FtpAccount).where(FtpAccount.account_id == account.id)) or 0
        mail_domain_ids = list(session.scalars(select(MailDomain.id).where(MailDomain.account_id == account.id)).all())
        email_count = session.scalar(select(func.count()).select_from(MailUser).where(MailUser.mail_domain_id.in_(mail_domain_ids))) if mail_domain_ids else 0
    counters = _cgroup_counters(account.username)
    return {
        **counters,
        "cpu_limit_cores": account.cpu_pct / 100,
        "memory_limit_bytes": account.mem_mb * 1024 * 1024,
        "io_limit_bytes_per_second": account.io_mb * 1024 * 1024,
        "process_limit": account.pids_max,
        "domain_count": domain_count,
        "subdomain_count": subdomain_count,
        "database_count": database_count,
        "email_account_count": email_count or 0,
        "ftp_account_count": ftp_count,
        "bandwidth_limit_bytes": limits.bandwidth_limit_mb * 1024 * 1024 if limits and limits.bandwidth_limit_mb else None,
        "database_limit": limits.database_limit if limits else None,
        "email_account_limit": limits.email_account_limit if limits else None,
        "subdomain_limit": limits.subdomain_limit if limits else None,
        "ftp_account_limit": limits.ftp_account_limit if limits else None,
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
        "resources": _resource_summary(account),
        "quota_soft_mb": account.quota_soft_mb,
        "quota_hard_mb": account.quota_hard_mb,
        "history": [_snapshot_to_dict(s) for s in history],
        "bandwidth_daily": [{"date": b.date, "bytes_served": b.bytes_served} for b in bandwidth_rows],
        "bandwidth_month_to_date_bytes": sum(
            b.bytes_served for b in bandwidth_rows if b.date.startswith(utcnow().strftime("%Y-%m"))
        ),
    }


# --- Phase 7b feature 2: bandwidth graphs (daily/weekly/monthly + top 5
# domains + admin cross-account ranking), built entirely on the BandwidthDaily/
# BandwidthDailyDomain rows refresh_bandwidth() above already populates from
# real OLS access-log bytes -- no separate data collection, just different
# ways of bucketing/ranking numbers that are already independently
# verifiable against the same logs an operator would grep by hand. ---------

BANDWIDTH_PERIODS = ("daily", "weekly", "monthly")
_PERIOD_LOOKBACK_DAYS = {"daily": 30, "weekly": 84, "monthly": 365}
TOP_DOMAINS_LIMIT = 5


def _validate_period(period: str) -> str:
    if period not in BANDWIDTH_PERIODS:
        raise ValueError(f"period must be one of {BANDWIDTH_PERIODS}")
    return period


def _period_cutoff(period: str) -> str:
    return (utcnow().date() - dt.timedelta(days=_PERIOD_LOOKBACK_DAYS[period])).isoformat()


def _bucket_label(date_str: str, period: str) -> str:
    if period == "daily":
        return date_str
    d = dt.date.fromisoformat(date_str)
    if period == "weekly":
        iso_year, iso_week, _ = d.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    return date_str[:7]  # monthly: "YYYY-MM"


def get_bandwidth_report(account: Account, period: str) -> dict:
    _validate_period(period)
    cutoff = _period_cutoff(period)

    with write_session() as session:
        daily_rows = session.scalars(
            select(BandwidthDaily).where(BandwidthDaily.account_id == account.id, BandwidthDaily.date >= cutoff)
        ).all()
        domain_rows = session.scalars(
            select(BandwidthDailyDomain).where(
                BandwidthDailyDomain.account_id == account.id, BandwidthDailyDomain.date >= cutoff
            )
        ).all()

    buckets: dict[str, int] = defaultdict(int)
    for row in daily_rows:
        buckets[_bucket_label(row.date, period)] += row.bytes_served

    domain_totals: dict[str, int] = defaultdict(int)
    for row in domain_rows:
        domain_totals[row.domain] += row.bytes_served
    top_domains = sorted(domain_totals.items(), key=lambda kv: kv[1], reverse=True)[:TOP_DOMAINS_LIMIT]

    return {
        "username": account.username,
        "period": period,
        "buckets": [{"label": label, "bytes_served": total} for label, total in sorted(buckets.items())],
        "top_domains": [{"domain": d, "bytes_served": total} for d, total in top_domains],
        "total_bytes_served": sum(buckets.values()),
    }


def get_bandwidth_ranking(period: str) -> dict:
    """Admin-wide view: every account ranked by total bandwidth served in
    the period, highest first -- built from the same account-level
    BandwidthDaily rows the per-account report uses, just summed across
    every account instead of scoped to one."""
    _validate_period(period)
    cutoff = _period_cutoff(period)

    with write_session() as session:
        rows = session.scalars(select(BandwidthDaily).where(BandwidthDaily.date >= cutoff)).all()
        account_ids = {r.account_id for r in rows}
        accounts_by_id = (
            {a.id: a.username for a in session.scalars(select(Account).where(Account.id.in_(account_ids))).all()}
            if account_ids
            else {}
        )

    totals: dict[int, int] = defaultdict(int)
    for row in rows:
        totals[row.account_id] += row.bytes_served

    ranking = sorted(
        (
            {"username": accounts_by_id.get(account_id, f"#{account_id}"), "bytes_served": total}
            for account_id, total in totals.items()
        ),
        key=lambda entry: entry["bytes_served"],
        reverse=True,
    )
    return {"period": period, "ranking": ranking}


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
