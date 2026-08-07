import datetime as dt

import pytest

from daemon import sitestats as ss
from shared.models import utcnow


def test_parse_log_line_extracts_all_fields():
    line = (
        '203.0.113.7 - - [01/Jul/2026:06:35:50 +0000] "GET /blog/post-1 HTTP/1.1" 200 4321 '
        '"https://google.com/search" "Mozilla/5.0"\n'
    )
    entry = ss._parse_log_line(line)
    assert entry["ip"] == "203.0.113.7"
    assert entry["date"] == "2026-07-01"
    assert entry["method"] == "GET"
    assert entry["path"] == "/blog/post-1"
    assert entry["status"] == 200
    assert entry["bytes"] == 4321
    assert entry["referer"] == "https://google.com/search"


def test_parse_log_line_handles_dash_bytes():
    line = '203.0.113.7 - - [01/Jul/2026:06:35:50 +0000] "GET / HTTP/1.1" 304 - "-" "-"\n'
    entry = ss._parse_log_line(line)
    assert entry["bytes"] == 0
    assert entry["referer"] == ""


def test_parse_log_line_ignores_malformed_lines():
    assert ss._parse_log_line("not a log line at all\n") is None


def test_compute_domain_day_stats_counts_pageviews_and_visitors():
    lines = [
        {"ip": "1.1.1.1", "date": "2026-07-01", "method": "GET", "path": "/", "status": 200, "bytes": 100, "referer": "", "ua": "x"},
        {"ip": "1.1.1.1", "date": "2026-07-01", "method": "GET", "path": "/about", "status": 200, "bytes": 200, "referer": "", "ua": "x"},
        {"ip": "2.2.2.2", "date": "2026-07-01", "method": "GET", "path": "/", "status": 200, "bytes": 100, "referer": "", "ua": "x"},
    ]
    stats = ss._compute_domain_day_stats(lines, "example.com")
    assert stats["pageviews"] == 3
    assert stats["unique_visitors"] == 2
    assert stats["bytes_served"] == 400


def test_compute_domain_day_stats_excludes_static_assets_from_pageviews():
    lines = [
        {"ip": "1.1.1.1", "date": "2026-07-01", "method": "GET", "path": "/style.css", "status": 200, "bytes": 50, "referer": "", "ua": "x"},
        {"ip": "1.1.1.1", "date": "2026-07-01", "method": "GET", "path": "/", "status": 200, "bytes": 100, "referer": "", "ua": "x"},
    ]
    stats = ss._compute_domain_day_stats(lines, "example.com")
    assert stats["pageviews"] == 1
    assert stats["bytes_served"] == 150  # bytes still counted for assets


def test_compute_domain_day_stats_counts_404s():
    lines = [
        {"ip": "1.1.1.1", "date": "2026-07-01", "method": "GET", "path": "/missing", "status": 404, "bytes": 10, "referer": "", "ua": "x"},
    ]
    stats = ss._compute_domain_day_stats(lines, "example.com")
    assert stats["error_404_count"] == 1
    assert stats["pageviews"] == 0  # 404s aren't pageviews


def test_compute_domain_day_stats_top_pages_ranked():
    lines = [{"ip": f"1.1.1.{i}", "date": "d", "method": "GET", "path": "/popular", "status": 200, "bytes": 1, "referer": "", "ua": "x"} for i in range(5)]
    lines += [{"ip": "9.9.9.9", "date": "d", "method": "GET", "path": "/rare", "status": 200, "bytes": 1, "referer": "", "ua": "x"}]
    stats = ss._compute_domain_day_stats(lines, "example.com")
    assert stats["top_pages"][0] == {"path": "/popular", "count": 5}


def test_referrer_host_excludes_own_domain():
    assert ss._referrer_host("https://example.com/page", "example.com") is None
    assert ss._referrer_host("https://sub.example.com/page", "example.com") is None
    assert ss._referrer_host("https://google.com/search", "example.com") == "google.com"
    assert ss._referrer_host("", "example.com") is None


def test_bucket_label_weekly_and_monthly():
    assert ss._bucket_label("2026-07-01", "daily") == "2026-07-01"
    assert ss._bucket_label("2026-07-01", "monthly") == "2026-07"
    assert ss._bucket_label("2026-07-01", "weekly").startswith("2026-W")


def test_refresh_domain_rejects_unknown_domain(isolated_db):
    with pytest.raises(RuntimeError):
        ss.refresh_domain("never-provisioned.example")


@pytest.fixture()
def domain_with_logs(isolated_db, tmp_path, monkeypatch):
    from shared.db import write_session
    from shared.models import Account, Domain

    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot=str(tmp_path / "public_html")))

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    vhost_name = "demo1_example"
    recent_timestamp = (utcnow().date() - dt.timedelta(days=1)).strftime("%d/%b/%Y")
    log_line = (
        f'203.0.113.7 - - [{recent_timestamp}:06:35:50 +0000] "GET / HTTP/1.1" 200 100 "-" "curl/8.0"\n'
        f'198.51.100.9 - - [{recent_timestamp}:06:36:50 +0000] "GET /missing HTTP/1.1" 404 0 "-" "curl/8.0"\n'
    )
    (logs_dir / f"{vhost_name}-access.log").write_text(log_line)

    monkeypatch.setattr(ss, "_all_access_log_files", lambda home_dir, vhost: [logs_dir / f"{vhost_name}-access.log"])
    return "demo1.example"


def test_refresh_domain_writes_daily_row(domain_with_logs):
    result = ss.refresh_domain(domain_with_logs)
    assert result["days_updated"] == 1

    stats = ss.get_stats({"domain": domain_with_logs, "period": "daily"})
    assert stats["total_pageviews"] == 1
    assert stats["buckets"][0]["error_404_count"] == 1


def test_refresh_domain_upserts_not_duplicates(domain_with_logs):
    ss.refresh_domain(domain_with_logs)
    ss.refresh_domain(domain_with_logs)  # run twice
    from shared.db import write_session
    from shared.models import SiteStatsDaily

    with write_session() as session:
        rows = session.query(SiteStatsDaily).filter_by(domain=domain_with_logs).all()
    assert len(rows) == 1  # not duplicated


def test_get_admin_summary_ranks_by_pageviews(domain_with_logs):
    ss.refresh_domain(domain_with_logs)
    result = ss.get_admin_summary({"period": "daily"})
    assert result["domains"][0]["domain"] == domain_with_logs
    assert result["server_total_pageviews"] == 1


def test_get_stats_rejects_bad_period(isolated_db):
    from shared.db import write_session
    from shared.models import Account, Domain

    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot="/x"))

    with pytest.raises(ValueError):
        ss.get_stats({"domain": "demo1.example", "period": "yearly"})
