"""QA round 2, item 9: admin-only PHP disable_functions overrides."""
import pytest
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import ols
from daemon import phpfunctions
from shared.db import write_session
from shared.models import Account, Domain


@pytest.fixture()
def stub_ols(monkeypatch):
    calls = []
    monkeypatch.setattr(ols, "refresh_vhost", lambda account: calls.append(account.username))
    return calls


@pytest.fixture()
def account_with_domains(isolated_db, monkeypatch, stub_ols):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    ha.create_account({"username": "demo1"})
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        session.add(Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot="/home/demo1/public_html"))
        session.add(Domain(account_id=account.id, domain="addon.example", kind="addon", docroot="/home/demo1/addon.example"))
    return {}


def test_get_overrides_reports_default_and_empty_when_none_set(account_with_domains):
    result = phpfunctions.get_overrides({"username": "demo1"})
    assert "exec" in result["default_disable_functions"]
    assert result["account_override"] is None
    assert result["domain_overrides"] == []


def test_set_account_wide_override(account_with_domains, stub_ols):
    result = phpfunctions.set_override({"username": "demo1", "domain": None, "disable_functions": ["exec"]})
    assert result["domain"] is None
    assert result["disable_functions"] == ["exec"]
    assert "demo1" in stub_ols  # refresh_vhost called

    overrides = phpfunctions.get_overrides({"username": "demo1"})
    assert overrides["account_override"]["disable_functions"] == ["exec"]


def test_set_account_wide_override_twice_upserts_not_duplicates(account_with_domains):
    phpfunctions.set_override({"username": "demo1", "domain": None, "disable_functions": ["exec"]})
    phpfunctions.set_override({"username": "demo1", "domain": None, "disable_functions": ["exec", "system"]})
    overrides = phpfunctions.get_overrides({"username": "demo1"})
    assert overrides["account_override"]["disable_functions"] == ["exec", "system"]
    assert overrides["domain_overrides"] == []


def test_set_domain_override(account_with_domains):
    result = phpfunctions.set_override({"username": "demo1", "domain": "demo1.example", "disable_functions": ["exec"]})
    assert result["domain"] == "demo1.example"
    overrides = phpfunctions.get_overrides({"username": "demo1"})
    assert len(overrides["domain_overrides"]) == 1
    assert overrides["domain_overrides"][0]["domain"] == "demo1.example"


def test_set_domain_override_rejects_unknown_domain(account_with_domains):
    with pytest.raises(RuntimeError, match="not found"):
        phpfunctions.set_override({"username": "demo1", "domain": "notmine.example", "disable_functions": ["exec"]})


def test_domain_override_and_account_wide_coexist_independently(account_with_domains):
    phpfunctions.set_override({"username": "demo1", "domain": None, "disable_functions": ["exec"]})
    phpfunctions.set_override({"username": "demo1", "domain": "demo1.example", "disable_functions": ["system"]})
    overrides = phpfunctions.get_overrides({"username": "demo1"})
    assert overrides["account_override"]["disable_functions"] == ["exec"]
    assert overrides["domain_overrides"][0]["disable_functions"] == ["system"]


def test_effective_disable_functions_domain_scope_wins_over_account_wide(account_with_domains):
    phpfunctions.set_override({"username": "demo1", "domain": None, "disable_functions": ["exec"]})
    phpfunctions.set_override({"username": "demo1", "domain": "demo1.example", "disable_functions": ["system"]})
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        # demo1.example has its own override -> wins over the account-wide one
        assert phpfunctions.effective_disable_functions(session, account.id, "demo1.example") == "system"
        # addon.example has no override of its own -> falls back to account-wide
        assert phpfunctions.effective_disable_functions(session, account.id, "addon.example") == "exec"


def test_effective_disable_functions_none_when_nothing_set(account_with_domains):
    with write_session() as session:
        account = session.scalar(select(Account).where(Account.username == "demo1"))
        assert phpfunctions.effective_disable_functions(session, account.id, "demo1.example") is None


def test_delete_override_removes_it(account_with_domains):
    phpfunctions.set_override({"username": "demo1", "domain": "demo1.example", "disable_functions": ["exec"]})
    phpfunctions.delete_override({"username": "demo1", "domain": "demo1.example"})
    overrides = phpfunctions.get_overrides({"username": "demo1"})
    assert overrides["domain_overrides"] == []


def test_delete_missing_override_raises(account_with_domains):
    with pytest.raises(RuntimeError, match="no PHP function override"):
        phpfunctions.delete_override({"username": "demo1", "domain": "demo1.example"})
