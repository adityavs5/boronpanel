import datetime as dt

import pytest
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import handlers_domain as hd
from daemon import usage
from daemon.procutil import ProcResult
from shared.db import write_session
from shared.models import BandwidthDaily, DatabaseGrant, MailDomain, UsageSnapshot, utcnow


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
    monkeypatch.setattr(hd, "ensure_docroot", lambda username, docroot: None)


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
