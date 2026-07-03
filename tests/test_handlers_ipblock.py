import pytest

from daemon import handlers_ipblock as hib
from shared.validation import ValidationError


@pytest.fixture()
def domain_row(isolated_db, monkeypatch):
    from shared.db import write_session
    from shared.models import Account, Domain

    monkeypatch.setattr(hib.ols, "refresh_vhost", lambda account: None)
    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        row = Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot="/home/demo1/public_html")
        session.add(row)
        session.flush()
    return row


def test_list_ip_blocks_starts_empty(domain_row):
    result = hib.list_ip_blocks({"domain": "demo1.example"})
    assert result["blocked"] == []


def test_add_ip_block(domain_row):
    result = hib.add_ip_block({"domain": "demo1.example", "entry": "203.0.113.7"})
    assert result["blocked"] == ["203.0.113.7"]
    assert hib.list_ip_blocks({"domain": "demo1.example"})["blocked"] == ["203.0.113.7"]


def test_add_ip_block_normalizes_cidr(domain_row):
    result = hib.add_ip_block({"domain": "demo1.example", "entry": "198.51.100.0/255.255.255.0"})
    assert result["blocked"] == ["198.51.100.0/24"]


def test_add_ip_block_rejects_duplicate(domain_row):
    hib.add_ip_block({"domain": "demo1.example", "entry": "203.0.113.7"})
    with pytest.raises(ValidationError):
        hib.add_ip_block({"domain": "demo1.example", "entry": "203.0.113.7"})


def test_add_ip_block_rejects_invalid_entry(domain_row):
    with pytest.raises(ValidationError):
        hib.add_ip_block({"domain": "demo1.example", "entry": "not-an-ip"})


def test_remove_ip_block(domain_row):
    hib.add_ip_block({"domain": "demo1.example", "entry": "203.0.113.7"})
    result = hib.remove_ip_block({"domain": "demo1.example", "entry": "203.0.113.7"})
    assert result["blocked"] == []


def test_remove_ip_block_rejects_absent_entry(domain_row):
    with pytest.raises(ValidationError):
        hib.remove_ip_block({"domain": "demo1.example", "entry": "203.0.113.7"})


def test_add_ip_block_calls_refresh_vhost(domain_row, monkeypatch):
    calls = []
    monkeypatch.setattr(hib.ols, "refresh_vhost", lambda account: calls.append(account.username))
    hib.add_ip_block({"domain": "demo1.example", "entry": "203.0.113.7"})
    assert calls == ["demo1"]


def test_add_ip_block_rejects_unknown_domain(isolated_db):
    with pytest.raises(RuntimeError):
        hib.add_ip_block({"domain": "never-provisioned.example", "entry": "203.0.113.7"})
