import os

import pytest

from daemon import handlers_account as ha
from daemon import logs
from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain
from shared.validation import ValidationError


@pytest.fixture()
def fake_account_home(tmp_path, monkeypatch):
    home_base = tmp_path / "home"
    home_base.mkdir()
    account_home = home_base / "demo1"
    (account_home / "logs").mkdir(parents=True)
    monkeypatch.setattr(settings, "home_base", str(home_base))
    return account_home


@pytest.fixture()
def account_with_domain(isolated_db, monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    ha.create_account({"username": "demo1"})
    with write_session() as session:
        from sqlalchemy import select

        account = session.scalar(select(Account).where(Account.username == "demo1"))
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot="/home/demo1/public_html"))
        session.add(Domain(account_id=account.id, domain="addon.demo1.example", kind="addon", docroot="/home/demo1/addon"))


def test_get_log_missing_file_returns_empty(account_with_domain, fake_account_home):
    result = logs.get_log({"username": "demo1", "type": "ols"})
    assert result["lines"] == []
    assert result["domain"] == "demo1.example"


def test_get_log_php_reads_shared_file(account_with_domain, fake_account_home):
    (fake_account_home / "logs" / "php-error.log").write_text("line1\nline2\nline3\n")
    result = logs.get_log({"username": "demo1", "type": "php"})
    assert result["lines"] == ["line1", "line2", "line3"]
    assert result["domain"] is None


def test_get_log_ols_reads_primary_domain_by_default(account_with_domain, fake_account_home):
    (fake_account_home / "logs" / "demo1_example-error.log").write_text("primary log line\n")
    (fake_account_home / "logs" / "addon_demo1_example-error.log").write_text("addon log line\n")

    result = logs.get_log({"username": "demo1", "type": "ols"})
    assert result["lines"] == ["primary log line"]


def test_get_log_ols_reads_specified_domain(account_with_domain, fake_account_home):
    (fake_account_home / "logs" / "demo1_example-error.log").write_text("primary log line\n")
    (fake_account_home / "logs" / "addon_demo1_example-error.log").write_text("addon log line\n")

    result = logs.get_log({"username": "demo1", "type": "ols", "domain": "addon.demo1.example"})
    assert result["lines"] == ["addon log line"]
    assert result["domain"] == "addon.demo1.example"


def test_get_log_rejects_domain_not_owned_by_account(account_with_domain, fake_account_home):
    with pytest.raises(RuntimeError):
        logs.get_log({"username": "demo1", "type": "ols", "domain": "someone-elses-domain.example"})


def test_get_log_rejects_invalid_type(account_with_domain, fake_account_home):
    with pytest.raises(ValidationError):
        logs.get_log({"username": "demo1", "type": "system"})


def test_get_log_filters_by_severity(account_with_domain, fake_account_home):
    (fake_account_home / "logs" / "php-error.log").write_text(
        "PHP Notice: something minor\nPHP Fatal error: big problem\nPHP Warning: watch out\n"
    )
    result = logs.get_log({"username": "demo1", "type": "php", "severity": "fatal"})
    assert result["lines"] == ["PHP Fatal error: big problem"]


def test_get_log_severity_filter_is_case_insensitive(account_with_domain, fake_account_home):
    (fake_account_home / "logs" / "php-error.log").write_text("PHP FATAL ERROR: big problem\n")
    result = logs.get_log({"username": "demo1", "type": "php", "severity": "fatal"})
    assert len(result["lines"]) == 1


def test_get_log_caps_at_500_lines(account_with_domain, fake_account_home):
    content = "\n".join(f"line {i}" for i in range(1000)) + "\n"
    (fake_account_home / "logs" / "php-error.log").write_text(content)
    result = logs.get_log({"username": "demo1", "type": "php"})
    assert len(result["lines"]) == 500
    assert result["lines"][-1] == "line 999"
    assert result["lines"][0] == "line 500"


def test_get_log_requested_lines_cannot_exceed_max(account_with_domain, fake_account_home):
    content = "\n".join(f"line {i}" for i in range(1000)) + "\n"
    (fake_account_home / "logs" / "php-error.log").write_text(content)
    result = logs.get_log({"username": "demo1", "type": "php", "lines": 5000})
    assert len(result["lines"]) == 500


def test_jailed_log_path_rejects_traversal_attempt(fake_account_home):
    with pytest.raises(logs.LogsError):
        logs._jailed_log_path("demo1", "../../../etc/passwd")


def test_get_log_requires_existing_account(isolated_db):
    with pytest.raises(RuntimeError):
        logs.get_log({"username": "nosuchaccount", "type": "php"})
