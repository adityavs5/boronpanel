import datetime as dt

import pytest
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import handlers_domain as hd
from daemon import usage
from daemon.procutil import ProcResult
from shared.db import write_session
from shared.models import Account, BandwidthDaily, BandwidthDailyDomain, DatabaseGrant, MailDomain, UsageSnapshot, utcnow


def _recent_date(days_ago: int = 1) -> str:
    """Keep reporting fixtures inside the production period windows."""
    return (utcnow().date() - dt.timedelta(days=days_ago)).isoformat()


@pytest.fixture()
def stub_sysops(monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)


@pytest.fixture()
def stub_ols(monkeypatch):
    monkeypatch.setattr(hd.ols, "provision_vhost", lambda account: None)


@pytest.fixture()
def stub_filesystem(monkeypatch):
    monkeypatch.setattr(hd, "ensure_docroot", lambda username, docroot, domain_name=None: None)


def _account(username="demo1"):
    with write_session() as session:
        return session.scalar(select(ha.Account).where(ha.Account.username == username))


def test_du_bytes_parses_output(monkeypatch, tmp_path):
    monkeypatch.setattr(usage, "run", lambda args, timeout=30: ProcResult(args=args, returncode=0, stdout="12345\t/some/path\n", stderr=""))
    assert usage._du_bytes(str(tmp_path)) == 12345


def test_du_bytes_missing_path_returns_zero():
    assert usage._du_bytes("/definitely/does/not/exist/xyz") == 0


def test_du_inodes_parses_output(monkeypatch, tmp_path):
    monkeypatch.setattr(usage, "run", lambda args, timeout=30: ProcResult(args=args, returncode=0, stdout="42\t/some/path\n", stderr=""))
    assert usage._du_inodes(str(tmp_path)) == 42


def test_process_count_counts_lines_regardless_of_exit_code(monkeypatch):
    # `ps -u <user>` exits 1 when zero processes match -- must not be
    # treated as an error, just zero processes.
    monkeypatch.setattr(usage, "run", lambda args, timeout=15: ProcResult(args=args, returncode=1, stdout="", stderr=""))
    assert usage._process_count("nobody-in-particular") == 0


def test_process_count_counts_real_output(monkeypatch):
    output = "  123 ?        00:00:01 php\n  124 ?        00:00:00 php\n"
    monkeypatch.setattr(usage, "run", lambda args, timeout=15: ProcResult(args=args, returncode=0, stdout=output, stderr=""))
    assert usage._process_count("demo1") == 2


def test_cgroup_counters_read_cpu_memory_and_io(monkeypatch, tmp_path):
    root = tmp_path / "boron-demo1.slice"
    root.mkdir()
    (root / "cpu.stat").write_text("usage_usec 2500000\nuser_usec 2000000\n")
    (root / "memory.current").write_text("1048576\n")
    (root / "pids.current").write_text("4\n")
    (root / "io.stat").write_text("8:0 rbytes=100 wbytes=200 rios=3 wios=4\n8:1 rbytes=10 wbytes=20 rios=1 wios=2\n")
    monkeypatch.setattr(usage, "CGROUP_ROOT", tmp_path)

    result = usage._cgroup_counters("demo1")

    assert result["cpu_usage_usec"] == 2500000
    assert result["memory_current_bytes"] == 1048576
    assert result["pids_current"] == 4
    assert (result["read_bytes"], result["write_bytes"]) == (110, 220)
    assert (result["read_ops"], result["write_ops"]) == (4, 6)


def test_parse_access_log_sums_bytes_by_date(tmp_path):
    log = tmp_path / "demo1-access.log"
    log.write_text(
        '1.2.3.4 - - [01/Jul/2026:06:35:50 +0000] "GET / HTTP/1.1" 200 1000 "-" "curl"\n'
        '1.2.3.4 - - [01/Jul/2026:07:00:00 +0000] "GET /a HTTP/1.1" 200 2000 "-" "curl"\n'
        '1.2.3.4 - - [02/Jul/2026:00:00:00 +0000] "GET /b HTTP/1.1" 200 500 "-" "curl"\n'
        'not a real log line at all\n'
        '1.2.3.4 - - [02/Jul/2026:00:00:01 +0000] "GET /c HTTP/1.1" 304 - "-" "curl"\n'
    )
    totals = usage._parse_access_log(log)
    assert totals == {"2026-07-01": 3000, "2026-07-02": 500}


def test_parse_access_log_missing_file_returns_empty(tmp_path):
    assert usage._parse_access_log(tmp_path / "nope.log") == {}


def test_refresh_bandwidth_upserts_not_duplicates(isolated_db, stub_sysops, stub_filesystem, stub_ols, monkeypatch, tmp_path):
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    account = _account()

    home_dir = tmp_path / "demo1"
    (home_dir / "logs").mkdir(parents=True)
    monkeypatch.setattr(usage.settings, "home_base", str(tmp_path))

    log = home_dir / "logs" / "demo1_example-access.log"
    log.write_text('1.2.3.4 - - [01/Jul/2026:06:35:50 +0000] "GET / HTTP/1.1" 200 1000 "-" "curl"\n')

    usage.refresh_bandwidth(account)
    with write_session() as session:
        row = session.scalar(select(BandwidthDaily).where(BandwidthDaily.account_id == account.id, BandwidthDaily.date == "2026-07-01"))
        assert row.bytes_served == 1000

    # Re-running with more data in the same log must UPDATE, not duplicate.
    log.write_text(
        '1.2.3.4 - - [01/Jul/2026:06:35:50 +0000] "GET / HTTP/1.1" 200 1000 "-" "curl"\n'
        '1.2.3.4 - - [01/Jul/2026:07:00:00 +0000] "GET /a HTTP/1.1" 200 500 "-" "curl"\n'
    )
    usage.refresh_bandwidth(account)
    with write_session() as session:
        rows = session.scalars(select(BandwidthDaily).where(BandwidthDaily.account_id == account.id, BandwidthDaily.date == "2026-07-01")).all()
        assert len(rows) == 1
        assert rows[0].bytes_served == 1500


def test_compute_live_usage_assembles_all_sources(isolated_db, stub_sysops, stub_filesystem, stub_ols, monkeypatch, tmp_path):
    ha.create_account({"username": "demo1"})
    account = _account()

    monkeypatch.setattr(usage, "_du_bytes", lambda path: 1000)
    monkeypatch.setattr(usage, "_du_inodes", lambda path: 7)
    monkeypatch.setattr(usage, "_process_count", lambda username: 3)
    monkeypatch.setattr(usage.mariadb, "database_size_bytes", lambda names: 555)

    with write_session() as session:
        session.add(MailDomain(account_id=account.id, domain="demo1.example"))
        session.add(DatabaseGrant(account_id=account.id, db_name="demo1_app", db_user="demo1_app"))

    result = usage.compute_live_usage(account)
    assert result["disk_home_bytes"] == 1000
    assert result["disk_mail_bytes"] == 1000  # one mail domain, _du_bytes stubbed to 1000
    assert result["disk_db_bytes"] == 555
    assert result["inode_count"] == 7
    assert result["process_count"] == 3


def test_get_usage_uses_cached_snapshot_when_fresh(isolated_db, stub_sysops, stub_filesystem, stub_ols, monkeypatch):
    ha.create_account({"username": "demo1"})
    account = _account()

    with write_session() as session:
        session.add(UsageSnapshot(account_id=account.id, taken_at=utcnow(), disk_home_bytes=42, disk_mail_bytes=0, disk_db_bytes=0, inode_count=1, process_count=1))

    def boom(account):
        raise AssertionError("should not recompute a fresh snapshot")

    monkeypatch.setattr(usage, "refresh_snapshot", boom)
    result = usage.get_usage(account)
    assert result["current"]["disk_home_bytes"] == 42


def test_get_usage_recomputes_when_stale(isolated_db, stub_sysops, stub_filesystem, stub_ols, monkeypatch):
    ha.create_account({"username": "demo1"})
    account = _account()

    stale_time = utcnow() - dt.timedelta(minutes=20)
    with write_session() as session:
        session.add(UsageSnapshot(account_id=account.id, taken_at=stale_time, disk_home_bytes=1, disk_mail_bytes=0, disk_db_bytes=0, inode_count=1, process_count=1))

    called = []
    monkeypatch.setattr(usage, "refresh_snapshot", lambda account: (called.append(account.username), {"disk_home_bytes": 999, "disk_mail_bytes": 0, "disk_db_bytes": 0, "disk_total_bytes": 999, "inode_count": 1, "process_count": 1, "taken_at": None})[1])
    result = usage.get_usage(account)
    assert called == ["demo1"]
    assert result["current"]["disk_home_bytes"] == 999


def test_get_usage_recomputes_when_no_snapshot_exists(isolated_db, stub_sysops, stub_filesystem, stub_ols, monkeypatch):
    ha.create_account({"username": "demo1"})
    account = _account()

    called = []
    monkeypatch.setattr(usage, "refresh_snapshot", lambda account: (called.append(account.username), {"disk_home_bytes": 0, "disk_mail_bytes": 0, "disk_db_bytes": 0, "disk_total_bytes": 0, "inode_count": 0, "process_count": 0, "taken_at": None})[1])
    usage.get_usage(account)
    assert called == ["demo1"]


def test_get_usage_force_refresh_ignores_fresh_cache(isolated_db, stub_sysops, stub_filesystem, stub_ols, monkeypatch):
    ha.create_account({"username": "demo1"})
    account = _account()

    with write_session() as session:
        session.add(UsageSnapshot(account_id=account.id, taken_at=utcnow(), disk_home_bytes=1, disk_mail_bytes=0, disk_db_bytes=0, inode_count=1, process_count=1))

    called = []
    monkeypatch.setattr(usage, "refresh_snapshot", lambda account: (called.append(account.username), {"disk_home_bytes": 0, "disk_mail_bytes": 0, "disk_db_bytes": 0, "disk_total_bytes": 0, "inode_count": 0, "process_count": 0, "taken_at": None})[1])
    usage.get_usage(account, force_refresh=True)
    assert called == ["demo1"]


def test_refresh_all_accounts_skips_terminated(isolated_db, stub_sysops, stub_filesystem, stub_ols, monkeypatch):
    ha.create_account({"username": "demo1"})
    ha.create_account({"username": "demo2"})
    ha.terminate_account({"username": "demo2"})

    refreshed = []
    monkeypatch.setattr(usage, "refresh_snapshot", lambda account: refreshed.append(account.username))
    count = usage.refresh_all_accounts()
    assert count == 1
    assert refreshed == ["demo1"]


# --- Phase 7b feature 2: bandwidth graphs -----------------------------------


def test_refresh_bandwidth_populates_per_domain_breakdown(isolated_db, stub_sysops, stub_filesystem, stub_ols, monkeypatch, tmp_path):
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "shop.example", "kind": "primary"})
    hd.add_domain({"username": "demo1", "domain": "blog.example", "kind": "addon"})
    account = _account()

    home_dir = tmp_path / "demo1"
    (home_dir / "logs").mkdir(parents=True)
    monkeypatch.setattr(usage.settings, "home_base", str(tmp_path))

    (home_dir / "logs" / "shop_example-access.log").write_text(
        '1.2.3.4 - - [01/Jul/2026:06:35:50 +0000] "GET / HTTP/1.1" 200 3000 "-" "curl"\n'
    )
    (home_dir / "logs" / "blog_example-access.log").write_text(
        '1.2.3.4 - - [01/Jul/2026:06:35:50 +0000] "GET / HTTP/1.1" 200 1000 "-" "curl"\n'
    )

    usage.refresh_bandwidth(account)

    with write_session() as session:
        rows = {
            r.domain: r.bytes_served
            for r in session.scalars(
                select(BandwidthDailyDomain).where(BandwidthDailyDomain.account_id == account.id, BandwidthDailyDomain.date == "2026-07-01")
            ).all()
        }
        assert rows == {"shop.example": 3000, "blog.example": 1000}
        # Account-level total must still equal the sum across domains.
        total_row = session.scalar(
            select(BandwidthDaily).where(BandwidthDaily.account_id == account.id, BandwidthDaily.date == "2026-07-01")
        )
        assert total_row.bytes_served == 4000

    # Re-running must UPDATE the per-domain rows too, not duplicate them.
    (home_dir / "logs" / "shop_example-access.log").write_text(
        '1.2.3.4 - - [01/Jul/2026:06:35:50 +0000] "GET / HTTP/1.1" 200 3000 "-" "curl"\n'
        '1.2.3.4 - - [01/Jul/2026:07:00:00 +0000] "GET /a HTTP/1.1" 200 500 "-" "curl"\n'
    )
    usage.refresh_bandwidth(account)
    with write_session() as session:
        shop_rows = session.scalars(
            select(BandwidthDailyDomain).where(
                BandwidthDailyDomain.account_id == account.id,
                BandwidthDailyDomain.domain == "shop.example",
                BandwidthDailyDomain.date == "2026-07-01",
            )
        ).all()
        assert len(shop_rows) == 1
        assert shop_rows[0].bytes_served == 3500


def test_bucket_label_daily_weekly_monthly():
    assert usage._bucket_label("2026-07-05", "daily") == "2026-07-05"
    assert usage._bucket_label("2026-07-05", "monthly") == "2026-07"
    # 2026-07-05 is a Sunday -- ISO week belongs to the week containing it.
    label = usage._bucket_label("2026-07-05", "weekly")
    assert label.startswith("2026-W")


def test_validate_period_rejects_unknown():
    with pytest.raises(ValueError):
        usage._validate_period("yearly")


def test_get_bandwidth_report_buckets_and_top_domains(isolated_db):
    date_one = _recent_date(1)
    date_two = _recent_date(2)
    with write_session() as session:
        account = Account(username="demo1", status="active")
        session.add(account)
        session.flush()
        account_id = account.id
        for date_str, total in ((date_one, 5000), (date_two, 3000)):
            session.add(BandwidthDaily(account_id=account_id, date=date_str, bytes_served=total))
        session.add(BandwidthDailyDomain(account_id=account_id, domain="shop.example", date=date_one, bytes_served=4000))
        session.add(BandwidthDailyDomain(account_id=account_id, domain="blog.example", date=date_one, bytes_served=1000))
        session.add(BandwidthDailyDomain(account_id=account_id, domain="shop.example", date=date_two, bytes_served=3000))

    account = _account()
    report = usage.get_bandwidth_report(account, "daily")
    assert report["period"] == "daily"
    assert report["total_bytes_served"] == 8000
    assert {"label": date_one, "bytes_served": 5000} in report["buckets"]
    assert {"label": date_two, "bytes_served": 3000} in report["buckets"]
    assert report["top_domains"][0] == {"domain": "shop.example", "bytes_served": 7000}
    assert report["top_domains"][1] == {"domain": "blog.example", "bytes_served": 1000}


def test_get_bandwidth_report_rejects_unknown_period(isolated_db):
    with write_session() as session:
        session.add(Account(username="demo1", status="active"))
    account = _account()
    with pytest.raises(ValueError):
        usage.get_bandwidth_report(account, "hourly")


def test_get_bandwidth_report_top_domains_capped_at_five(isolated_db):
    recent_date = _recent_date()
    with write_session() as session:
        account = Account(username="demo1", status="active")
        session.add(account)
        session.flush()
        for i in range(8):
            session.add(
                BandwidthDailyDomain(account_id=account.id, domain=f"site{i}.example", date=recent_date, bytes_served=(8 - i) * 100)
            )
    account = _account()
    report = usage.get_bandwidth_report(account, "daily")
    assert len(report["top_domains"]) == 5
    assert report["top_domains"][0]["domain"] == "site0.example"


def test_get_bandwidth_ranking_orders_accounts_by_total(isolated_db):
    with write_session() as session:
        a1 = Account(username="demo1", status="active")
        a2 = Account(username="demo2", status="active")
        session.add_all([a1, a2])
        session.flush()
        session.add(BandwidthDaily(account_id=a1.id, date="2026-07-01", bytes_served=1000))
        session.add(BandwidthDaily(account_id=a2.id, date="2026-07-01", bytes_served=9000))
        session.add(BandwidthDaily(account_id=a1.id, date="2026-07-02", bytes_served=500))

    ranking = usage.get_bandwidth_ranking("monthly")
    assert ranking["ranking"][0] == {"username": "demo2", "bytes_served": 9000}
    assert ranking["ranking"][1] == {"username": "demo1", "bytes_served": 1500}


def test_get_bandwidth_ranking_excludes_old_data_outside_period(isolated_db):
    with write_session() as session:
        account = Account(username="demo1", status="active")
        session.add(account)
        session.flush()
        old_date = (utcnow().date() - dt.timedelta(days=400)).isoformat()
        session.add(BandwidthDaily(account_id=account.id, date=old_date, bytes_served=99999))

    ranking = usage.get_bandwidth_ranking("monthly")
    assert ranking["ranking"] == []
