import pytest

from daemon import handlers_account as ha
from daemon import handlers_php_ini as hpi
from shared.validation import ValidationError


@pytest.fixture()
def stub_ols(monkeypatch):
    calls = []
    monkeypatch.setattr(hpi.ols, "refresh_vhost", lambda account: calls.append(account.username))
    return calls


@pytest.fixture()
def account(isolated_db, monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    ha.create_account({"username": "demo1"})


def test_get_php_ini_defaults_when_unset(account, stub_ols):
    result = hpi.get_php_ini({"username": "demo1"})
    assert result["php_ini"] is None
    assert result["defaults"]["memory_limit"] == "256M"


def test_set_php_ini_happy_path(account, stub_ols):
    result = hpi.set_php_ini(
        {
            "username": "demo1",
            "memory_limit": "512M",
            "upload_max_filesize": "128M",
            "post_max_size": "128M",
            "max_execution_time": 60,
            "display_errors": True,
            "error_reporting": "E_ALL",
        }
    )
    assert result["php_ini"]["memory_limit"] == "512M"
    assert result["php_ini"]["display_errors"] is True
    assert "demo1" in stub_ols

    fetched = hpi.get_php_ini({"username": "demo1"})["php_ini"]
    assert fetched["memory_limit"] == "512M"


def test_set_php_ini_partial_update_keeps_other_fields(account, stub_ols):
    hpi.set_php_ini({"username": "demo1", "memory_limit": "512M"})
    result = hpi.set_php_ini({"username": "demo1", "max_execution_time": 90})
    assert result["php_ini"]["memory_limit"] == "512M"  # unchanged from first call
    assert result["php_ini"]["max_execution_time"] == 90


def test_set_php_ini_rejects_unlimited_memory(account, stub_ols):
    with pytest.raises(ValidationError):
        hpi.set_php_ini({"username": "demo1", "memory_limit": "-1"})


def test_set_php_ini_rejects_unlimited_execution_time(account, stub_ols):
    with pytest.raises(ValidationError):
        hpi.set_php_ini({"username": "demo1", "max_execution_time": 0})


def test_set_php_ini_rejects_oversized_memory(account, stub_ols):
    with pytest.raises(ValidationError):
        hpi.set_php_ini({"username": "demo1", "memory_limit": "99999M"})


def test_set_php_ini_rejects_malformed_size(account, stub_ols):
    with pytest.raises(ValidationError):
        hpi.set_php_ini({"username": "demo1", "memory_limit": "lots"})


def test_set_php_ini_rejects_post_max_size_below_upload_max(account, stub_ols):
    with pytest.raises(ValidationError):
        hpi.set_php_ini({"username": "demo1", "upload_max_filesize": "128M", "post_max_size": "64M"})


def test_set_php_ini_rejects_invalid_error_reporting_injection_attempt(account, stub_ols):
    with pytest.raises(ValidationError):
        hpi.set_php_ini({"username": "demo1", "error_reporting": 'E_ALL"; rm -rf /'})


def test_reset_php_ini(account, stub_ols):
    hpi.set_php_ini({"username": "demo1", "memory_limit": "512M"})
    result = hpi.reset_php_ini({"username": "demo1"})
    assert result["status"] == "reset_to_defaults"
    assert hpi.get_php_ini({"username": "demo1"})["php_ini"] is None


def test_reset_php_ini_idempotent_when_never_set(account, stub_ols):
    hpi.reset_php_ini({"username": "demo1"})  # must not raise


def test_terminate_account_php_ini(account, stub_ols):
    hpi.set_php_ini({"username": "demo1", "memory_limit": "512M"})

    from sqlalchemy import select

    from shared.db import write_session
    from shared.models import Account

    with write_session() as session:
        acct = session.scalar(select(Account).where(Account.username == "demo1"))
        hpi.terminate_account_php_ini(acct)

    assert hpi.get_php_ini({"username": "demo1"})["php_ini"] is None
