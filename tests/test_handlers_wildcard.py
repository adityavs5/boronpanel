import pytest

from daemon import handlers_wildcard as hw


@pytest.fixture()
def domain_row(isolated_db, monkeypatch):
    from shared.db import write_session
    from shared.models import Account, Domain

    monkeypatch.setattr(hw.ols, "refresh_vhost", lambda account: None)
    monkeypatch.setattr(hw.settings, "server_public_ip", "104.234.179.64")
    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        row = Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot="/home/demo1/public_html")
        session.add(row)
        session.flush()
    return row


def test_get_wildcard_defaults_disabled(domain_row, monkeypatch):
    monkeypatch.setattr(hw.dnsprovider, "zone_exists", lambda zone: True)
    result = hw.get_wildcard({"domain": "demo1.example"})
    assert result["enabled"] is False
    assert result["zone_managed"] is True


def test_set_wildcard_requires_managed_zone(domain_row, monkeypatch):
    monkeypatch.setattr(hw.dnsprovider, "zone_exists", lambda zone: False)
    with pytest.raises(RuntimeError, match="managed DNS zone"):
        hw.set_wildcard({"domain": "demo1.example", "enabled": True})


def test_set_wildcard_creates_dns_record_when_enabled(domain_row, monkeypatch):
    monkeypatch.setattr(hw.dnsprovider, "zone_exists", lambda zone: True)
    calls = []
    monkeypatch.setattr(hw.dnsprovider, "upsert_record", lambda zone, sub, rtype, values: calls.append((zone, sub, rtype, values)))
    result = hw.set_wildcard({"domain": "demo1.example", "enabled": True})
    assert result["enabled"] is True
    assert result["dns_record_created"] is True
    assert calls == [("demo1.example", "*", "A", ["104.234.179.64"])]


def test_set_wildcard_calls_refresh_vhost(domain_row, monkeypatch):
    monkeypatch.setattr(hw.dnsprovider, "zone_exists", lambda zone: True)
    monkeypatch.setattr(hw.dnsprovider, "upsert_record", lambda *a, **k: None)
    calls = []
    monkeypatch.setattr(hw.ols, "refresh_vhost", lambda account: calls.append(account.username))
    hw.set_wildcard({"domain": "demo1.example", "enabled": True})
    assert calls == ["demo1"]


def test_disabling_wildcard_removes_dns_record(domain_row, monkeypatch):
    monkeypatch.setattr(hw.dnsprovider, "zone_exists", lambda zone: True)
    monkeypatch.setattr(hw.dnsprovider, "upsert_record", lambda *a, **k: None)
    hw.set_wildcard({"domain": "demo1.example", "enabled": True})

    delete_calls = []
    monkeypatch.setattr(hw.dnsprovider, "delete_record", lambda zone, sub, rtype: delete_calls.append((zone, sub, rtype)))
    result = hw.set_wildcard({"domain": "demo1.example", "enabled": False})
    assert result["enabled"] is False
    assert result["dns_record_created"] is False
    assert delete_calls == [("demo1.example", "*", "A")]


def test_get_wildcard_reports_existing_subdomains(domain_row, monkeypatch, isolated_db):
    from shared.db import write_session
    from shared.models import Domain

    monkeypatch.setattr(hw.dnsprovider, "zone_exists", lambda zone: True)
    with write_session() as session:
        session.add(Domain(account_id=domain_row.account_id, domain="blog.demo1.example", kind="subdomain", docroot="/home/demo1/blog"))

    result = hw.get_wildcard({"domain": "demo1.example"})
    assert result["existing_subdomains"] == ["blog.demo1.example"]


def test_set_wildcard_rejects_unknown_domain(isolated_db):
    with pytest.raises(RuntimeError):
        hw.set_wildcard({"domain": "never-provisioned.example", "enabled": True})
