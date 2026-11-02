import pytest

from daemon import handlers_wildcard as hw

SERVER_IP = "104.234.179.64"


@pytest.fixture()
def domain_row(isolated_db, monkeypatch):
    from shared.db import write_session
    from shared.models import Account, Domain

    monkeypatch.setattr(hw.ols, "refresh_vhost", lambda account: None)
    monkeypatch.setattr(hw.settings, "server_public_ip", SERVER_IP)
    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        row = Domain(account_id=account.id, domain="demo1.example", kind="primary", docroot="/home/demo1/public_html")
        session.add(row)
        session.flush()
    return row


@pytest.fixture()
def zone(domain_row, monkeypatch):
    """In-memory demo1.example zone behind the dnsprovider surface."""
    records: dict[tuple[str, str], list[str]] = {}
    calls = {"upsert": [], "delete": []}

    def name(sub):
        return "demo1.example" if sub in ("@", "") else f"{sub}.demo1.example"

    def upsert_record(zone_name, sub, rtype, values, **kwargs):
        calls["upsert"].append((zone_name, sub, rtype, list(values)))
        records[(name(sub), rtype)] = list(values)

    def delete_record(zone_name, sub, rtype):
        calls["delete"].append((zone_name, sub, rtype))
        records.pop((name(sub), rtype), None)

    monkeypatch.setattr(hw.dnsprovider, "zone_exists", lambda zone_name: True)
    monkeypatch.setattr(hw.dnsprovider, "upsert_record", upsert_record)
    monkeypatch.setattr(hw.dnsprovider, "delete_record", delete_record)
    monkeypatch.setattr(hw.dnsprovider, "list_records", lambda zone_name: [
        {"name": n, "type": t, "ttl": 3600, "values": v} for (n, t), v in records.items()
    ])
    return {"records": records, "calls": calls}


def test_get_wildcard_defaults_disabled(zone):
    result = hw.get_wildcard({"domain": "demo1.example"})
    assert result["enabled"] is False
    assert result["zone_managed"] is True


def test_set_wildcard_requires_managed_zone(domain_row, monkeypatch):
    monkeypatch.setattr(hw.dnsprovider, "zone_exists", lambda zone: False)
    with pytest.raises(RuntimeError, match="managed DNS zone"):
        hw.set_wildcard({"domain": "demo1.example", "enabled": True})


def test_set_wildcard_creates_dns_record_when_enabled(zone):
    result = hw.set_wildcard({"domain": "demo1.example", "enabled": True})
    assert result["enabled"] is True
    assert result["dns_record_created"] is True
    assert zone["calls"]["upsert"] == [("demo1.example", "*", "A", [SERVER_IP])]


def test_set_wildcard_calls_refresh_vhost(zone, monkeypatch):
    calls = []
    monkeypatch.setattr(hw.ols, "refresh_vhost", lambda account: calls.append(account.username))
    hw.set_wildcard({"domain": "demo1.example", "enabled": True})
    assert calls == ["demo1"]


def test_disabling_wildcard_removes_dns_record(zone):
    hw.set_wildcard({"domain": "demo1.example", "enabled": True})
    result = hw.set_wildcard({"domain": "demo1.example", "enabled": False})
    assert result["enabled"] is False
    assert result["dns_record_created"] is False
    assert zone["calls"]["delete"] == [("demo1.example", "*", "A")]
    assert ("*.demo1.example", "A") not in zone["records"]


def test_get_wildcard_reports_existing_subdomains(zone, domain_row):
    from shared.db import write_session
    from shared.models import Domain

    with write_session() as session:
        session.add(Domain(account_id=domain_row.account_id, domain="blog.demo1.example", kind="subdomain", docroot="/home/demo1/blog"))

    result = hw.get_wildcard({"domain": "demo1.example"})
    assert result["existing_subdomains"] == ["blog.demo1.example"]


def test_set_wildcard_rejects_unknown_domain(isolated_db):
    with pytest.raises(RuntimeError):
        hw.set_wildcard({"domain": "never-provisioned.example", "enabled": True})


# --- Live-test regressions: the zone, not the flag, decides --------------


def test_reenable_recreates_a_record_deleted_outside_the_toggle(zone):
    hw.set_wildcard({"domain": "demo1.example", "enabled": True})
    zone["records"].clear()  # e.g. DELETE /dns/zones/<d>/records?subdomain=*&type=A
    assert hw.get_wildcard({"domain": "demo1.example"})["dns_record_created"] is False
    result = hw.set_wildcard({"domain": "demo1.example", "enabled": True})
    assert result["dns_record_created"] is True
    assert zone["records"][("*.demo1.example", "A")] == [SERVER_IP]


def test_users_own_wildcard_record_is_kept_on_enable_and_disable(zone):
    zone["records"][("*.demo1.example", "A")] = ["192.0.2.99"]
    enabled = hw.set_wildcard({"domain": "demo1.example", "enabled": True})
    assert enabled["enabled"] is True
    assert enabled["dns_record_created"] is False
    assert "192.0.2.99" in enabled["dns_conflict"]
    assert zone["records"][("*.demo1.example", "A")] == ["192.0.2.99"]
    assert hw.get_wildcard({"domain": "demo1.example"})["dns_conflict"]
    hw.set_wildcard({"domain": "demo1.example", "enabled": False})
    assert zone["records"][("*.demo1.example", "A")] == ["192.0.2.99"]
    assert zone["calls"] == {"upsert": [], "delete": []}


def test_record_changed_by_user_after_enable_survives_disable(zone):
    hw.set_wildcard({"domain": "demo1.example", "enabled": True})
    zone["records"][("*.demo1.example", "A")] = ["192.0.2.50"]
    hw.set_wildcard({"domain": "demo1.example", "enabled": False})
    assert zone["records"][("*.demo1.example", "A")] == ["192.0.2.50"]


def test_identical_user_record_is_not_claimed_by_the_toggle(zone):
    zone["records"][("*.demo1.example", "A")] = [SERVER_IP]
    assert hw.set_wildcard({"domain": "demo1.example", "enabled": True})["dns_record_created"] is False
    hw.set_wildcard({"domain": "demo1.example", "enabled": False})
    assert zone["records"][("*.demo1.example", "A")] == [SERVER_IP]


def test_user_wildcard_cname_is_a_conflict(zone):
    zone["records"][("*.demo1.example", "CNAME")] = ["elsewhere.example."]
    result = hw.set_wildcard({"domain": "demo1.example", "enabled": True})
    assert result["dns_conflict"] and zone["calls"]["upsert"] == []


def test_expanded_ipv6_server_address_matches_the_panel_record(zone, monkeypatch):
    """The provider returns '2001:db8::1'; an expanded setting must not turn
    the panel's own record into a 'user record' conflict."""
    monkeypatch.setattr(hw.settings, "server_public_ip", "2001:0db8:0000::0001")
    hw.set_wildcard({"domain": "demo1.example", "enabled": True})
    assert zone["records"][("*.demo1.example", "AAAA")] == ["2001:db8::1"]
    state = hw.get_wildcard({"domain": "demo1.example"})
    assert state["dns_record_created"] is True and state["dns_conflict"] is None
    hw.set_wildcard({"domain": "demo1.example", "enabled": False})
    assert ("*.demo1.example", "AAAA") not in zone["records"]
