import pytest

from daemon import handlers_hotlink as hh
from shared.validation import ValidationError


@pytest.fixture()
def domain_row(isolated_db, monkeypatch):
    from shared.db import write_session
    from shared.models import Account, Domain

    monkeypatch.setattr(hh.ols, "refresh_vhost", lambda account: None)
    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        row = Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot="/home/demo1/public_html")
        session.add(row)
        session.flush()
    return row


def test_get_hotlink_protection_defaults_disabled(domain_row):
    result = hh.get_hotlink_protection({"domain": "demo1.example"})
    assert result["enabled"] is False
    assert result["allowed_domains"] == []


def test_set_hotlink_protection_enables_with_allowed_domains(domain_row):
    result = hh.set_hotlink_protection({"domain": "demo1.example", "enabled": True, "allowed_domains": ["cdn.example"]})
    assert result["enabled"] is True
    assert result["allowed_domains"] == ["cdn.example"]
    assert hh.get_hotlink_protection({"domain": "demo1.example"}) == result


def test_set_hotlink_protection_calls_refresh_vhost(domain_row, monkeypatch):
    calls = []
    monkeypatch.setattr(hh.ols, "refresh_vhost", lambda account: calls.append(account.username))
    hh.set_hotlink_protection({"domain": "demo1.example", "enabled": True})
    assert calls == ["demo1"]


def test_set_hotlink_protection_rejects_invalid_allowed_domain(domain_row):
    with pytest.raises(ValidationError):
        hh.set_hotlink_protection({"domain": "demo1.example", "enabled": True, "allowed_domains": ["not a domain"]})


def test_set_hotlink_protection_dedupes_allowed_domains(domain_row):
    result = hh.set_hotlink_protection(
        {"domain": "demo1.example", "enabled": True, "allowed_domains": ["cdn.example", "cdn.example", "Cdn.Example"]}
    )
    assert result["allowed_domains"] == ["cdn.example"]


def test_get_hotlink_protection_rejects_unknown_domain(isolated_db):
    with pytest.raises(RuntimeError):
        hh.get_hotlink_protection({"domain": "never-provisioned.example"})
