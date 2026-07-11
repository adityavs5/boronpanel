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
`scripts/sitestats_snapshot.py` on a daily cron (`deploy/forgehost-
sitestats.cron`), mirroring `scripts/usage_snapshot.py`'s own thin-wrapper-
over-a-daemon-function shape.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from pathlib import Path

from sqlalchemy import select

from shared.db import write_session
from shared.models import Account, Domain, SiteStatsDaily, utcnow
from shared.validation import validate_domain

from daemon import geoip, ols
from daemon.usage import _all_access_log_files  # reuse the exact log-file-glob convention

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
    if month_num is None:
        return None
    return {
        "ip": ip,
        "date": f"{year}-{month_num:02d}-{day}",
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

    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if not host or host == own_domain or host.endswith(f".{own_domain}"):
        return None  # internal navigation, not a real referrer
    return host


def _compute_domain_day_stats(lines: list[dict], domain_name: str) -> dict:
    pageviews = 0
    visitors: set[str] = set()
    bytes_served = 0
    error_404 = 0
    page_counter: Counter = Counter()
    referrer_counter: Counter = Counter()
    country_counter: Counter = Counter()

    for entry in lines:
        bytes_served += entry["bytes"]
        visitors.add(entry["ip"])
        if entry["status"] == 404:
            error_404 += 1
        is_asset = bool(_STATIC_ASSET_RE.search(entry["path"]))
        if entry["status"] < 400 and not is_asset:
            pageviews += 1
            page_counter[entry["path"]] += 1
        ref_host = _referrer_host(entry["referer"], domain_name)
        if ref_host:
            referrer_counter[ref_host] += 1
        country = geoip.lookup_country(entry["ip"])
        if country:
            country_counter[country] += 1

    return {
        "pageviews": pageviews,
        "unique_visitors": len(visitors),
        "bytes_served": bytes_served,
        "error_404_count": error_404,
        "top_pages": [{"path": p, "count": c} for p, c in page_counter.most_common(TOP_N)],
        "top_referrers": [{"referrer": r, "count": c} for r, c in referrer_counter.most_common(TOP_N)],
        "top_countries": [{"country_code": cc, "count": c} for cc, c in country_counter.most_common(TOP_N)],
    }


def _parse_domain_logs(home_dir: str, domain_name: str) -> dict[str, list[dict]]:
    """Groups every parsed log line for this domain by calendar date --
    mirrors daemon/usage.refresh_bandwidth's own "one pass over every log
    file, bucket by date" shape."""
    vhost_name = ols._vhost_name(domain_name)
    by_date: dict[str, list[dict]] = {}
    for log_file in _all_access_log_files(home_dir, vhost_name):
        try:
            with Path(log_file).open("r", errors="replace") as f:
                for line in f:
                    entry = _parse_log_line(line)
                    if entry is None:
                        continue
                    by_date.setdefault(entry["date"], []).append(entry)
        except OSError:
            continue
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

    home_dir = f"/home/{username}"
    by_date = _parse_domain_logs(home_dir, domain_name)
    if not by_date:
        return {"domain": domain_name, "days_updated": 0}

    with write_session() as session:
        for date_key, lines in by_date.items():
            stats = _compute_domain_day_stats(lines, domain_name)
            existing = session.scalar(
                select(SiteStatsDaily).where(SiteStatsDaily.domain == domain_name, SiteStatsDaily.date == date_key)
            )
            if existing is None:
                existing = SiteStatsDaily(account_id=account_id, domain=domain_name, date=date_key)
                session.add(existing)
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
        except RuntimeError:
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
        rows = session.scalars(
            select(SiteStatsDaily).where(SiteStatsDaily.domain == domain_name, SiteStatsDaily.date >= cutoff).order_by(SiteStatsDaily.date)
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
