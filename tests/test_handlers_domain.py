import pytest
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import handlers_domain as hd
from shared.db import write_session
from shared.models import Domain


@pytest.fixture()
def stub_sysops(monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)


@pytest.fixture()
def stub_filesystem(monkeypatch):
    monkeypatch.setattr(hd, "_ensure_docroot", lambda username, docroot: None)


def test_add_domain_success_calls_provision_vhost(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    calls = []
    monkeypatch.setattr(hd.ols, "provision_vhost", lambda account, domains: calls.append((account.username, domains)))

    ha.create_account({"username": "demo1"})
    result = hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    assert result["domain"] == "demo1.example"
    assert calls == [("demo1", ["demo1.example"])]

    with write_session() as session:
        account = session.scalar(select(ha.Account).where(ha.Account.username == "demo1"))
        assert account.primary_domain == "demo1.example"


def test_add_domain_compensates_db_row_when_ols_apply_fails(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    def boom(account, domains):
        raise RuntimeError("openlitespeed -t failed")

    monkeypatch.setattr(hd.ols, "provision_vhost", boom)

    ha.create_account({"username": "demo1"})
    with pytest.raises(RuntimeError):
        hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    with write_session() as session:
        orphan = session.scalar(select(Domain).where(Domain.domain == "demo1.example"))
        assert orphan is None, "a failed OLS apply must not leave an orphaned Domain row"

        account = session.scalar(select(ha.Account).where(ha.Account.username == "demo1"))
        assert account.primary_domain is None, "primary_domain must be cleared along with the orphaned row"


def test_add_domain_rejects_duplicate_domain(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    monkeypatch.setattr(hd.ols, "provision_vhost", lambda account, domains: None)
    ha.create_account({"username": "demo1"})
    ha.create_account({"username": "demo2"})
    hd.add_domain({"username": "demo1", "domain": "shared.example", "kind": "primary"})
    with pytest.raises(RuntimeError):
        hd.add_domain({"username": "demo2", "domain": "shared.example", "kind": "primary"})


def test_add_domain_rejects_unknown_account(isolated_db, stub_sysops, stub_filesystem):
    with pytest.raises(RuntimeError):
        hd.add_domain({"username": "ghost", "domain": "ghost.example"})


def test_list_domains(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    monkeypatch.setattr(hd.ols, "provision_vhost", lambda account, domains: None)
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    hd.add_domain({"username": "demo1", "domain": "addon.example", "kind": "addon"})
    result = hd.list_domains({"username": "demo1"})
    domains = {d["domain"] for d in result["domains"]}
    assert domains == {"demo1.example", "addon.example"}
