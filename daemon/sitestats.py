"""Per-domain site statistics (missing-features batch, goal feature 6),
parsed from the same OLS access logs `daemon/usage.py` already reads for
bandwidth tracking -- a NEW, independent regex/parse pass here rather than
extending `daemon/usage._ACCESS_LOG_RE`'s existing capture groups, since
that regex's group *positions* are relied on by usage.py's own (live,
already-shipped) bandwidth parsing; touching it risks a silent regression
there for no benefit -- this feature needs several additional fields
(method, path, status, referer, UA) that regex was never designed to
capture, so a second, purpose-built regex over the same log files is the
lower-risk choice.

Daily snapshots are upserted into `SiteStatsDaily` (one row per (domain,
date)) by `refresh_domain`/`refresh_all`, the same "recompute and replace
this day's row while its raw log lines are still on disk" semantics
`daemon/usage.py`'s `BandwidthDaily` already documents -- triggered by
`scripts/sitestats_snapshot.py` on a daily cron (`deploy/boron-
sitestats.cron`), mirroring `scripts/usage_snapshot.py`'s own thin-wrapper-
over-a-daemon-function shape.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
import re
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain, SiteStatsDaily, utcnow
from shared.validation import validate_domain

from daemon import geoip, ols, safeio
from daemon.usage import _all_access_log_files, access_log_lines  # reuse the exact log-file-glob convention

# IP - - [01/Jul/2026:06:35:50 +0000] "METHOD /path HTTP/1.1" status bytes "referer" "user-agent"
_ACCESS_LOG_RE = re.compile(
    r'^(\S+) \S+ \S+ \[(\d{2})/(\w{3})/(\d{4}):\d{2}:\d{2}:\d{2} [+-]\d{4}\] '
    r'"(\S+) (\S+)[^"]*" (\d+) (\d+|-) "([^"]*)" "([^"]*)"'
)
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
)}
# A hit for one of these extensions counts toward bandwidth/bytes but not
# toward "pageviews" or the top-pages breakdown -- the same page-vs-asset
# distinction every reference web analytics tool (AWStats included, per
# RESEARCH.md's own survey of reference panels) draws.
_STATIC_ASSET_RE = re.compile(
    r'\.(css|js|mjs|png|jpe?g|gif|svg|ico|webp|woff2?|ttf|eot|map|mp4|webm|pdf)(\?|$)', re.IGNORECASE
)
TOP_N = 10
HISTORY_DAYS = 90


def _parse_log_line(line: str) -> dict | None:
    m = _ACCESS_LOG_RE.match(line)
    if not m:
        return None
    ip, day, mon, year, method, path, status, byte_str, referer, ua = m.groups()
    month_num = _MONTHS.get(mon)
    if month_num is None or len(status) > 3 or len(byte_str) > 18:
        return None
    try:
        date = dt.date(int(year), month_num, int(day))
    except ValueError:
        return None
    return {
        "ip": ip,
        "date": date.isoformat(),
        "method": method,
        "path": path.split("?", 1)[0],
        "status": int(status),
        "bytes": 0 if byte_str == "-" else int(byte_str),
        "referer": referer if referer and referer != "-" else "",
        "ua": ua,
    }


def _referrer_host(url: str, own_domain: str) -> str | None:
    if not url:
        return None
    import urllib.parse

    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    if not host or host == own_domain or host.endswith(f".{own_domain}"):
        return None  # internal navigation, not a real referrer
    return host


MAX_STAT_KEYS = 200_000
MAX_STAT_KEY_BYTES = 16 * 1024 * 1024


class _DayStats:
    def __init__(self, domain, budget):
        self.domain, self.budget = domain, budget
        self.pageviews = self.bytes = self.errors = 0
        self.visitors = set()
        self.pages, self.referrers, self.countries = Counter(), Counter(), Counter()

    def _remember(self, collection, key):
        if key not in collection:
            self.budget[0] += 1
            self.budget[1] += len(key.encode('utf-8'))
            if self.budget[0] > MAX_STAT_KEYS or self.budget[1] > MAX_STAT_KEY_BYTES:
                raise ValueError('Site statistics exceeded the distinct-key budget')
        if isinstance(collection, set):
            collection.add(key)
        else:
            collection[key] += 1

    def add(self, entry):
        self.bytes += entry['bytes']
        if self.bytes > 2**63 - 1:
            raise ValueError('Site statistics byte total exceeds database limits')
        self._remember(self.visitors, entry['ip'])
        self.errors += entry['status'] == 404
        if entry['status'] < 400 and not _STATIC_ASSET_RE.search(entry['path']):
            self.pageviews += 1
            self._remember(self.pages, entry['path'])
        referrer = _referrer_host(entry['referer'], self.domain)
        if referrer:
            self._remember(self.referrers, referrer)
        country = geoip.lookup_country(entry['ip'])
        if country:
            self._remember(self.countries, country)

    def result(self):
        return dict(pageviews=self.pageviews, unique_visitors=len(self.visitors),
            bytes_served=self.bytes, error_404_count=self.errors,
            top_pages=[dict(path=p, count=c) for p, c in self.pages.most_common(TOP_N)],
            top_referrers=[dict(referrer=p, count=c) for p, c in self.referrers.most_common(TOP_N)],
            top_countries=[dict(country_code=p, count=c) for p, c in self.countries.most_common(TOP_N)])


def _compute_domain_day_stats(lines: list[dict], domain_name: str) -> dict:
    stats = _DayStats(domain_name, [0, 0])
    for entry in lines:
        stats.add(entry)
    return stats.result()


def _parse_domain_logs(home_dir: str, domain_name: str) -> dict[str, _DayStats]:
    vhost_name = ols._vhost_name(domain_name)
    by_date = {}
    budget = [0, 0]
    deadline = time.monotonic() + 30
    oldest = (utcnow().date() - dt.timedelta(days=366)).isoformat()
    newest = (utcnow().date() + dt.timedelta(days=1)).isoformat()
    for log_file in _all_access_log_files(home_dir, vhost_name):
        for line in access_log_lines(log_file):
            if time.monotonic() > deadline:
                raise ValueError('Site statistics scan exceeded its resource budget')
            entry = _parse_log_line(line)
            if entry is None or not oldest <= entry['date'] <= newest:
                continue
            if entry['date'] not in by_date:
                by_date[entry['date']] = _DayStats(domain_name, budget)
            by_date[entry['date']].add(entry)
    return by_date


def refresh_domain(domain_name: str) -> dict:
    domain_name = validate_domain(domain_name)
    with write_session() as session:
        domain_row = session.scalar(select(Domain).where(Domain.domain == domain_name))
        if domain_row is None:
            raise RuntimeError(f"domain '{domain_name}' not found")
        account = session.get(Account, domain_row.account_id)
        if account is None:
            raise RuntimeError(f"domain '{domain_name}' has no owning account")
        account_id, username = account.id, account.username

    home_dir = str(Path(settings.home_base) / username)
    by_date = _parse_domain_logs(home_dir, domain_name)
    if not by_date:
        return {"domain": domain_name, "days_updated": 0}

    with write_session() as session:
        current = session.scalar(select(Domain).where(Domain.domain == domain_name))
        if current is None or current.account_id != account_id:
            raise RuntimeError('Site ownership changed during statistics scan')
        for date_key, accumulator in by_date.items():
            stats = accumulator.result()
            existing = session.scalar(
                select(SiteStatsDaily).where(SiteStatsDaily.domain == domain_name, SiteStatsDaily.date == date_key)
            )
            if existing is None:
                existing = SiteStatsDaily(account_id=account_id, domain=domain_name, date=date_key)
                session.add(existing)
            existing.account_id = account_id
            for key, value in stats.items():
                setattr(existing, key, value)
    return {"domain": domain_name, "days_updated": len(by_date)}


def refresh_all() -> int:
    """scripts/sitestats_snapshot.py's entry point (daily cron)."""
    with write_session() as session:
        domains = list(session.scalars(select(Domain.domain)).all())
    count = 0
    for domain_name in domains:
        try:
            refresh_domain(domain_name)
            count += 1
        except (RuntimeError, OSError, ValueError, safeio.UnsafePathError):
            logging.getLogger(__name__).exception('Site statistics scan failed for %s', domain_name)
            continue  # domain removed mid-run, or no owning account -- skip, not fatal
    return count


PERIODS = ("daily", "weekly", "monthly")
_PERIOD_LOOKBACK_DAYS = {"daily": 30, "weekly": 90, "monthly": 365}


def _bucket_label(date_str: str, period: str) -> str:
    if period == "daily":
        return date_str
    d = dt.date.fromisoformat(date_str)
    if period == "weekly":
        iso_year, iso_week, _ = d.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    return date_str[:7]


def _merge_top(rows: list[SiteStatsDaily], attr: str, key_field: str) -> list[dict]:
    counter: Counter = Counter()
    for row in rows:
        for entry in getattr(row, attr) or []:
            counter[entry[key_field]] += entry["count"]
    return [{key_field: k, "count": c} for k, c in counter.most_common(TOP_N)]


def get_stats(params: dict) -> dict:
    domain_name = validate_domain(params["domain"])
    period = params.get("period", "daily")
    if period not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}")
    cutoff = (utcnow().date() - dt.timedelta(days=_PERIOD_LOOKBACK_DAYS[period])).isoformat()

    with write_session() as session:
        owner = session.scalar(select(Domain.account_id).where(Domain.domain == domain_name))
        if owner is None:
            raise RuntimeError('Site is no longer provisioned')
        rows = session.scalars(
            select(SiteStatsDaily).where(SiteStatsDaily.domain == domain_name,
                SiteStatsDaily.account_id == owner, SiteStatsDaily.date >= cutoff).order_by(SiteStatsDaily.date)
        ).all()

    buckets: dict[str, dict] = {}
    for row in rows:
        label = _bucket_label(row.date, period)
        b = buckets.setdefault(label, {"label": label, "pageviews": 0, "unique_visitors": 0, "bytes_served": 0, "error_404_count": 0})
        b["pageviews"] += row.pageviews
        b["unique_visitors"] += row.unique_visitors  # approximate for weekly/monthly (not deduped across days)
        b["bytes_served"] += row.bytes_served
        b["error_404_count"] += row.error_404_count

    return {
        "domain": domain_name,
        "period": period,
        "buckets": [buckets[k] for k in sorted(buckets)],
        "top_pages": _merge_top(rows, "top_pages", "path"),
        "top_referrers": _merge_top(rows, "top_referrers", "referrer"),
        "top_countries": _merge_top(rows, "top_countries", "country_code"),
        "geoip_configured": geoip.is_configured(),
        "total_pageviews": sum(r.pageviews for r in rows),
        "total_bytes_served": sum(r.bytes_served for r in rows),
        "total_unique_visitors_approx": sum(r.unique_visitors for r in rows),
    }


def get_admin_summary(params: dict) -> dict:
    """Admin: server-wide summary (goal's explicit requirement) -- every
    domain's totals over the requested period, ranked by pageviews."""
    period = params.get("period", "daily")
    if period not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}")
    cutoff = (utcnow().date() - dt.timedelta(days=_PERIOD_LOOKBACK_DAYS[period])).isoformat()

    with write_session() as session:
        rows = session.scalars(select(SiteStatsDaily).where(SiteStatsDaily.date >= cutoff)).all()

    totals: dict[str, dict] = {}
    for row in rows:
        t = totals.setdefault(row.domain, {"domain": row.domain, "pageviews": 0, "unique_visitors": 0, "bytes_served": 0, "error_404_count": 0})
        t["pageviews"] += row.pageviews
        t["unique_visitors"] += row.unique_visitors
        t["bytes_served"] += row.bytes_served
        t["error_404_count"] += row.error_404_count

    ranked = sorted(totals.values(), key=lambda t: t["pageviews"], reverse=True)
    return {
        "period": period,
        "domains": ranked,
        "server_total_pageviews": sum(t["pageviews"] for t in ranked),
        "server_total_bytes_served": sum(t["bytes_served"] for t in ranked),
        "geoip_configured": geoip.is_configured(),
    }


def configure_geoip(params: dict) -> dict:
    license_key = params.get("license_key", "")
    geoip.download_database(license_key)
    return {"geoip_configured": geoip.is_configured()}
