import pytest
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import handlers_usage as hu
from shared.db import write_session
from shared.models import BandwidthDaily


@pytest.fixture()
def stub_sysops(monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)


def test_get_bandwidth_unknown_account_raises(isolated_db):
    with pytest.raises(RuntimeError):
        hu.get_bandwidth({"username": "ghost1", "period": "daily"})


def test_get_bandwidth_delegates_to_usage_module(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    with write_session() as session:
        account = session.scalar(select(ha.Account).where(ha.Account.username == "demo1"))
        session.add(BandwidthDaily(account_id=account.id, date="2026-07-01", bytes_served=500))

    result = hu.get_bandwidth({"username": "demo1", "period": "daily"})
    assert result["username"] == "demo1"
    assert result["total_bytes_served"] == 500


def test_get_bandwidth_defaults_to_daily_period(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    result = hu.get_bandwidth({"username": "demo1"})
    assert result["period"] == "daily"


def test_get_bandwidth_ranking_defaults_to_monthly(isolated_db):
    result = hu.get_bandwidth_ranking()
    assert result["period"] == "monthly"
    assert result["ranking"] == []


def test_get_bandwidth_ranking_accepts_explicit_period(isolated_db):
    result = hu.get_bandwidth_ranking({"period": "weekly"})
    assert result["period"] == "weekly"
