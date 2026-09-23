import pytest
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import usage_alerts as ua
from shared.db import write_session
from shared.models import (
    Account,
    DatabaseGrant,
    Domain,
    MailDomain,
    MailUser,
    UsageAlert,
    UsageSnapshot,
    utcnow,
)


@pytest.fixture()
def stub_sysops(monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    monkeypatch.setattr(ha.sysops, "lock_user", lambda username: None)
    monkeypatch.setattr(ha.sysops, "unlock_user", lambda username: None)


def _account(username="demo1"):
    with write_session() as session:
        return session.scalar(select(Account).where(Account.username == username))


def _seed_snapshot(account_id, disk_total_bytes, bandwidth_mtd_bytes=0):
    """usage.get_usage() reads the latest UsageSnapshot + BandwidthDaily
    rows -- seed both directly rather than going through the real du/OLS-
    log path, matching test_usage.py's own convention."""
    with write_session() as session:
        session.add(
            UsageSnapshot(
                account_id=account_id, taken_at=utcnow(),
                disk_home_bytes=disk_total_bytes, disk_mail_bytes=0, disk_db_bytes=0,
                inode_count=1, process_count=1,
            )
        )
    if bandwidth_mtd_bytes:
        from shared.models import BandwidthDaily

        with write_session() as session:
            session.add(
                BandwidthDaily(account_id=account_id, date=utcnow().strftime("%Y-%m-%d"), bytes_served=bandwidth_mtd_bytes)
            )


# --- limits CRUD -------------------------------------------------------------


def test_get_limits_creates_default_row_lazily(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    data = ua.get_limits({"username": "demo1"})
    assert data["bandwidth_limit_mb"] is None
    assert data["auto_suspend_at_100"] is False


def test_set_limits_partial_update(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    ua.set_limits({"username": "demo1", "database_limit": 5})
    data = ua.get_limits({"username": "demo1"})
    assert data["database_limit"] == 5
    assert data["bandwidth_limit_mb"] is None  # untouched


def test_set_limits_rejects_zero_or_negative():
    with pytest.raises(Exception):
        ua.validate_resource_limit(0, "database_limit")
    with pytest.raises(Exception):
        ua.validate_resource_limit(-5, "database_limit")


def test_set_limits_none_clears_limit(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    ua.set_limits({"username": "demo1", "database_limit": 5})
    ua.set_limits({"username": "demo1", "database_limit": None})
    assert ua.get_limits({"username": "demo1"})["database_limit"] is None


def test_get_limits_unknown_account_raises(isolated_db):
    with pytest.raises(RuntimeError):
        ua.get_limits({"username": "ghost1"})


# --- resource counting -------------------------------------------------------


def test_database_count(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    account = _account()
    with write_session() as session:
        session.add(DatabaseGrant(account_id=account.id, db_name="demo1_a", db_user="demo1_a"))
        session.add(DatabaseGrant(account_id=account.id, db_name="demo1_b", db_user="demo1_b"))
    assert ua._database_count(account.id) == 2


def test_email_account_count(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    account = _account()
    with write_session() as session:
        md = MailDomain(account_id=account.id, domain="demo1.example")
        session.add(md)
        session.flush()
        session.add(MailUser(mail_domain_id=md.id, local_part="a", domain="demo1.example"))
        session.add(MailUser(mail_domain_id=md.id, local_part="b", domain="demo1.example"))
    assert ua._email_account_count(account.id) == 2


def test_subdomain_count_only_counts_subdomain_kind(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    account = _account()
    with write_session() as session:
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot="/x"))
        session.add(Domain(account_id=account.id, domain="addon.example", kind="addon", docroot="/y"))
        session.add(Domain(account_id=account.id, domain="sub.demo1.example", kind="subdomain", docroot="/z"))
    assert ua._subdomain_count(account.id) == 1


# --- threshold crossing logic ------------------------------------------------


def test_highest_crossed_thresholds():
    assert ua._highest_crossed(105) == 100
    assert ua._highest_crossed(100) == 100
    assert ua._highest_crossed(95) == 90
    assert ua._highest_crossed(85) == 80
    assert ua._highest_crossed(79.9) is None
    assert ua._highest_crossed(0) is None


# --- check_usage_alerts end to end -------------------------------------------


def test_check_usage_alerts_fires_disk_alert_at_80_percent(isolated_db, stub_sysops, monkeypatch):
    ha.create_account({"username": "demo1"})
    account = _account()
    with write_session() as session:
        a = session.get(Account, account.id)
        a.quota_hard_mb = 100  # 100 MB hard quota
    _seed_snapshot(account.id, disk_total_bytes=85 * 1024 * 1024)  # 85% of 100MB

    emitted = []
    monkeypatch.setattr(ua.events, "emit", lambda event_type, acc, **ctx: emitted.append((event_type, ctx["resource"], ctx["threshold_pct"])))

    new_alerts = ua.check_usage_alerts(_account())
    assert len(new_alerts) == 1
    assert new_alerts[0]["resource"] == "disk"
    assert new_alerts[0]["threshold_pct"] == 80
    assert emitted == [("usage.limit.reached", "disk", 80)]

    with write_session() as session:
        rows = session.scalars(select(UsageAlert).where(UsageAlert.account_id == account.id)).all()
        assert len(rows) == 1
        assert rows[0].resolved_at is None


def test_check_usage_alerts_does_not_refire_same_threshold(isolated_db, stub_sysops, monkeypatch):
    ha.create_account({"username": "demo1"})
    account = _account()
    with write_session() as session:
        session.get(Account, account.id).quota_hard_mb = 100
    _seed_snapshot(account.id, disk_total_bytes=85 * 1024 * 1024)

    emitted = []
    monkeypatch.setattr(ua.events, "emit", lambda *a, **k: emitted.append(1))

    first = ua.check_usage_alerts(_account())
    second = ua.check_usage_alerts(_account())
    assert len(first) == 1
    assert len(second) == 0
    assert len(emitted) == 1


def test_check_usage_alerts_escalates_from_80_to_90(isolated_db, stub_sysops, monkeypatch):
    ha.create_account({"username": "demo1"})
    account = _account()
    with write_session() as session:
        session.get(Account, account.id).quota_hard_mb = 100
    _seed_snapshot(account.id, disk_total_bytes=85 * 1024 * 1024)
    monkeypatch.setattr(ua.events, "emit", lambda *a, **k: None)
    ua.check_usage_alerts(_account())

    with write_session() as session:
        first_alert = session.scalar(select(UsageAlert).where(UsageAlert.account_id == account.id))
        first_alert_id = first_alert.id

    # Usage climbs further -- force a fresh snapshot at 92%.
    _seed_snapshot(account.id, disk_total_bytes=92 * 1024 * 1024)
    new_alerts = ua.check_usage_alerts(_account())
    assert len(new_alerts) == 1
    assert new_alerts[0]["threshold_pct"] == 90

    with write_session() as session:
        old = session.get(UsageAlert, first_alert_id)
        assert old.resolved_at is not None, "the 80% episode must be closed out when it escalates to 90%"
        all_rows = session.scalars(select(UsageAlert).where(UsageAlert.account_id == account.id)).all()
        assert len(all_rows) == 2


def test_check_usage_alerts_resolves_when_usage_drops_below_80(isolated_db, stub_sysops, monkeypatch):
    ha.create_account({"username": "demo1"})
    account = _account()
    with write_session() as session:
        session.get(Account, account.id).quota_hard_mb = 100
    _seed_snapshot(account.id, disk_total_bytes=85 * 1024 * 1024)
    monkeypatch.setattr(ua.events, "emit", lambda *a, **k: None)
    ua.check_usage_alerts(_account())

    _seed_snapshot(account.id, disk_total_bytes=10 * 1024 * 1024)  # dropped back to 10%
    ua.check_usage_alerts(_account())

    with write_session() as session:
        rows = session.scalars(select(UsageAlert).where(UsageAlert.account_id == account.id)).all()
        assert len(rows) == 1
        assert rows[0].resolved_at is not None


def test_check_usage_alerts_skips_resources_with_no_limit_set(isolated_db, stub_sysops, monkeypatch):
    ha.create_account({"username": "demo1"})
    account = _account()
    with write_session() as session:
        session.get(Account, account.id).quota_hard_mb = 100
    _seed_snapshot(account.id, disk_total_bytes=1024)  # negligible disk usage
    # No bandwidth/database/email/subdomain limits configured at all.
    emitted = []
    monkeypatch.setattr(ua.events, "emit", lambda *a, **k: emitted.append(1))
    new_alerts = ua.check_usage_alerts(_account())
    assert new_alerts == []
    assert emitted == []


def test_check_usage_alerts_database_count_threshold(isolated_db, stub_sysops, monkeypatch):
    ha.create_account({"username": "demo1"})
    account = _account()
    with write_session() as session:
        session.get(Account, account.id).quota_hard_mb = 100
        for i in range(9):
            session.add(DatabaseGrant(account_id=account.id, db_name=f"demo1_db{i}", db_user=f"demo1_db{i}"))
    _seed_snapshot(account.id, disk_total_bytes=1024)
    ua.set_limits({"username": "demo1", "database_limit": 10})

    emitted = []
    monkeypatch.setattr(ua.events, "emit", lambda event_type, acc, **ctx: emitted.append(ctx["resource"]))
    new_alerts = ua.check_usage_alerts(_account())
    assert any(a["resource"] == "databases" and a["threshold_pct"] == 90 for a in new_alerts)
    assert "databases" in emitted


def test_check_usage_alerts_auto_suspends_at_100_when_enabled(isolated_db, stub_sysops, monkeypatch):
    ha.create_account({"username": "demo1"})
    account = _account()
    with write_session() as session:
        session.get(Account, account.id).quota_hard_mb = 100
    _seed_snapshot(account.id, disk_total_bytes=100 * 1024 * 1024)  # exactly 100%
    ua.set_limits({"username": "demo1", "auto_suspend_at_100": True})
    monkeypatch.setattr(ua.events, "emit", lambda *a, **k: None)

    ua.check_usage_alerts(_account())

    with write_session() as session:
        refreshed = session.scalar(select(Account).where(Account.username == "demo1"))
        assert refreshed.status == "suspended"


def test_check_usage_alerts_does_not_auto_suspend_by_default(isolated_db, stub_sysops, monkeypatch):
    ha.create_account({"username": "demo1"})
    account = _account()
    with write_session() as session:
        session.get(Account, account.id).quota_hard_mb = 100
    _seed_snapshot(account.id, disk_total_bytes=100 * 1024 * 1024)
    monkeypatch.setattr(ua.events, "emit", lambda *a, **k: None)

    ua.check_usage_alerts(_account())

    with write_session() as session:
        refreshed = session.scalar(select(Account).where(Account.username == "demo1"))
        assert refreshed.status == "active"


def test_get_alerts_separates_active_from_history(isolated_db, stub_sysops, monkeypatch):
    ha.create_account({"username": "demo1"})
    account = _account()
    with write_session() as session:
        session.get(Account, account.id).quota_hard_mb = 100
    monkeypatch.setattr(ua.events, "emit", lambda *a, **k: None)

    _seed_snapshot(account.id, disk_total_bytes=85 * 1024 * 1024)
    ua.check_usage_alerts(_account())
    _seed_snapshot(account.id, disk_total_bytes=10 * 1024 * 1024)
    ua.check_usage_alerts(_account())

    data = ua.get_alerts({"username": "demo1"})
    assert len(data["alerts"]) == 1
    assert len(data["active"]) == 0  # resolved


def test_check_all_accounts_skips_terminated(isolated_db, stub_sysops, monkeypatch):
    ha.create_account({"username": "demo1"})
    ha.create_account({"username": "demo2"})
    ha.terminate_account({"username": "demo2"})
    monkeypatch.setattr(ua.events, "emit", lambda *a, **k: None)

    checked = []
    real_check = ua.check_usage_alerts

    def spy(account):
        checked.append(account.username)
        return real_check(account)

    monkeypatch.setattr(ua, "check_usage_alerts", spy)
    ua.check_all_accounts()
    assert checked == ["demo1"]


@pytest.mark.parametrize('initially_enabled', [False, True])
def test_bandwidth_enforcement_survives_existing_alert(isolated_db, stub_sysops, monkeypatch, initially_enabled):
    ha.create_account({'username': 'demo1'})
    account = _account()
    _seed_snapshot(account.id, disk_total_bytes=0, bandwidth_mtd_bytes=101 * 1024 * 1024)
    ua.set_limits({'username': 'demo1', 'bandwidth_limit_mb': 100, 'auto_suspend_at_100': initially_enabled})
    monkeypatch.setattr(ua.events, 'emit', lambda *a, **kw: None)
    attempts = []
    def suspend(params):
        attempts.append(params)
        if initially_enabled and len(attempts) == 1:
            raise RuntimeError('temporary service failure')
        with write_session() as session:
            session.get(Account, account.id).status = 'suspended'
    monkeypatch.setattr(ha, 'suspend_account', suspend)
    monkeypatch.setattr(ua.audit, 'record_account_event', lambda *a, **kw: None)
    assert len(ua.check_usage_alerts(_account())) == 1
    ua.set_limits({'username': 'demo1', 'auto_suspend_at_100': True})
    assert ua.check_usage_alerts(_account()) == []
    assert _account().status == 'suspended'
    count = len(attempts)
    assert ua.check_usage_alerts(_account()) == []
    assert len(attempts) == count
