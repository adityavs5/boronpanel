"""Phase 8 feature 3: parked (alias) domains."""
import pytest
from sqlalchemy import select

from daemon import parked
from shared.db import write_session
from shared.models import Account, Domain, ParkedDomain


def _setup(monkeypatch, username="demo1"):
    monkeypatch.setattr(parked.ols, "provision_vhost", lambda a: None)
    monkeypatch.setattr(parked.ols, "remove_domain_vhost", lambda a, d: None)
    with write_session() as db:
        account = Account(username=username, status="active", uid=5001, gid=5001, primary_domain="site.com")
        db.add(account)
        db.flush()
        db.add(Domain(account_id=account.id, domain="site.com", kind="primary", docroot="/home/demo1/public_html"))
        return account.id


def test_add_parked_defaults_to_primary(isolated_db, monkeypatch):
    account_id = _setup(monkeypatch)
    result = parked.add_parked_domain({"username": "demo1", "parked_domain": "alias.com"})
    assert result["target_domain"] == "site.com"
    assert result["docroot"] == "/home/demo1/public_html"
    with write_session() as db:
        d = db.scalar(select(Domain).where(Domain.domain == "alias.com"))
        assert d.kind == "parked"
        assert d.docroot == "/home/demo1/public_html"  # SAME docroot as target
        p = db.scalar(select(ParkedDomain).where(ParkedDomain.parked_domain == "alias.com"))
        assert p.target_domain == "site.com"


def test_add_parked_explicit_target(isolated_db, monkeypatch):
    account_id = _setup(monkeypatch)
    with write_session() as db:
        db.add(Domain(account_id=account_id, domain="addon.com", kind="addon", docroot="/home/demo1/addon.com"))
    result = parked.add_parked_domain({"username": "demo1", "parked_domain": "alias.com", "target_domain": "addon.com"})
    assert result["docroot"] == "/home/demo1/addon.com"


def test_add_parked_rejects_used_domain(isolated_db, monkeypatch):
    _setup(monkeypatch)
    with pytest.raises(RuntimeError, match="already in use"):
        parked.add_parked_domain({"username": "demo1", "parked_domain": "site.com"})


def test_add_parked_rejects_park_on_park(isolated_db, monkeypatch):
    _setup(monkeypatch)
    parked.add_parked_domain({"username": "demo1", "parked_domain": "alias1.com"})
    with pytest.raises(RuntimeError, match="onto another parked"):
        parked.add_parked_domain({"username": "demo1", "parked_domain": "alias2.com", "target_domain": "alias1.com"})


def test_list_parked(isolated_db, monkeypatch):
    _setup(monkeypatch)
    parked.add_parked_domain({"username": "demo1", "parked_domain": "a.com"})
    parked.add_parked_domain({"username": "demo1", "parked_domain": "b.com"})
    result = parked.list_parked_domains({"username": "demo1"})
    names = {p["parked_domain"] for p in result["parked_domains"]}
    assert names == {"a.com", "b.com"}


def test_parked_domain_reports_target_suspension(isolated_db, monkeypatch):
    _setup(monkeypatch)
    parked.add_parked_domain({"username":"demo1","parked_domain":"alias.com"})
    with write_session() as db:
        target=db.scalar(select(Domain).where(Domain.domain=="site.com"))
        target.suspended=True;target.suspension_reason="Maintenance window"
    item=parked.list_parked_domains({"username":"demo1"})["parked_domains"][0]
    assert item["effective_suspended"] is True
    assert item["suspension_reason"]=="Maintenance window"


def test_remove_parked(isolated_db, monkeypatch):
    account_id = _setup(monkeypatch)
    parked.add_parked_domain({"username": "demo1", "parked_domain": "alias.com"})
    parked.remove_parked_domain({"username": "demo1", "parked_domain": "alias.com"})
    with write_session() as db:
        assert db.scalar(select(Domain).where(Domain.domain == "alias.com")) is None
        assert db.scalar(select(ParkedDomain).where(ParkedDomain.parked_domain == "alias.com")) is None


def test_add_parked_rolls_back_on_ols_failure(isolated_db, monkeypatch):
    account_id = _setup(monkeypatch)

    def boom(a):
        raise RuntimeError("ols failed")

    monkeypatch.setattr(parked.ols, "provision_vhost", boom)
    with pytest.raises(RuntimeError, match="ols failed"):
        parked.add_parked_domain({"username": "demo1", "parked_domain": "alias.com"})
    # Rolled back: no Domain and no ParkedDomain row left behind.
    with write_session() as db:
        assert db.scalar(select(Domain).where(Domain.domain == "alias.com")) is None
        assert db.scalar(select(ParkedDomain).where(ParkedDomain.parked_domain == "alias.com")) is None


def test_terminate_cleans_parked_rows(isolated_db, monkeypatch):
    account_id = _setup(monkeypatch)
    parked.add_parked_domain({"username": "demo1", "parked_domain": "alias.com"})
    with write_session() as db:
        account = db.get(Account, account_id)
        parked.terminate_account_parked(account)
    with write_session() as db:
        assert db.scalar(select(ParkedDomain).where(ParkedDomain.account_id == account_id)) is None
