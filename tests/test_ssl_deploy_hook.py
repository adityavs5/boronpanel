import importlib.util
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import handlers_domain as hd
from shared.db import write_session
from shared.models import Account, Domain

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "ssl_deploy_hook.py"


def _load_hook_module():
    spec = importlib.util.spec_from_file_location("ssl_deploy_hook", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ssl_deploy_hook"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def hook(monkeypatch):
    module = _load_hook_module()
    monkeypatch.setattr(module.ols, "refresh_vhost", lambda account: None)
    monkeypatch.setattr(module.ols, "refresh_webmail_vhost", lambda: None)
    return module


@pytest.fixture()
def account_with_domain(isolated_db, monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)
    monkeypatch.setattr(hd, "ensure_docroot", lambda username, docroot, domain_name=None: None)
    monkeypatch.setattr(hd.ols, "provision_vhost", lambda account: None)
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})


def test_plain_renewal_sets_active_not_wildcard(hook, account_with_domain, monkeypatch):
    monkeypatch.setenv("RENEWED_DOMAINS", "demo1.example")
    assert hook.main() == 0
    with write_session() as session:
        domain = session.scalar(select(Domain).where(Domain.domain == "demo1.example"))
        assert domain.ssl_status == "active"
        assert domain.ssl_is_wildcard is False


def test_wildcard_renewal_sets_active_and_wildcard_flag(hook, account_with_domain, monkeypatch):
    monkeypatch.setenv("RENEWED_DOMAINS", "demo1.example *.demo1.example")
    assert hook.main() == 0
    with write_session() as session:
        domain = session.scalar(select(Domain).where(Domain.domain == "demo1.example"))
        assert domain.ssl_status == "active"
        assert domain.ssl_is_wildcard is True


def test_wildcard_prefixed_name_is_never_looked_up_as_its_own_row(hook, account_with_domain, monkeypatch, caplog):
    """The '*.<domain>' entry in RENEWED_DOMAINS must never itself be
    treated as a Domain row lookup key (Boron never stores a
    wildcard-prefixed domain name) -- only used to derive is_wildcard for
    the real, bare-name row."""
    monkeypatch.setenv("RENEWED_DOMAINS", "demo1.example *.demo1.example")
    hook.main()
    assert "*.demo1.example' not found" not in caplog.text


def test_missing_renewed_domains_env_returns_error(hook, monkeypatch):
    monkeypatch.delenv("RENEWED_DOMAINS", raising=False)
    assert hook.main() == 1


def test_unknown_domain_is_skipped_not_fatal(hook, isolated_db, monkeypatch):
    monkeypatch.setenv("RENEWED_DOMAINS", "never-provisioned.example")
    assert hook.main() == 0
