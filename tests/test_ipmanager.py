from types import SimpleNamespace

import pytest

from daemon import ipmanager
from shared.db import write_session
from shared.models import Account, DnsZone, Domain, ServerIp, ServerIpAssignment
from shared.validation import ValidationError


def _addresses(*items):
    return {
        "lo": [SimpleNamespace(family=2, address="127.0.0.1", netmask="255.0.0.0")],
        "eth0": [SimpleNamespace(family=2 if ":" not in address else 10, address=address, netmask=mask) for address, mask in items],
    }


@pytest.fixture()
def inventory(isolated_db, monkeypatch):
    monkeypatch.setattr(ipmanager.settings, "server_public_ip", "198.51.100.10")
    monkeypatch.setattr(ipmanager.psutil, "net_if_addrs", lambda: _addresses(
        ("198.51.100.10", "255.255.255.0"),
        ("198.51.100.11", "255.255.255.0"),
        ("2001:db8::10", "ffff:ffff:ffff:ffff::"),
    ))
    with write_session() as session:
        session.add(Account(username="alpha", status="active"))
        session.add(Account(username="beta", status="active"))
    ipmanager.import_addresses({"addresses": ["198.51.100.10", "198.51.100.11"]})
    return ipmanager.list_state()


def test_detection_filters_loopback_and_imports_host_addresses(inventory):
    assert {row["address"] for row in inventory["ips"]} == {"198.51.100.10", "198.51.100.11"}
    assert inventory["ips"][0]["present_on_host"] is True
    assert inventory["detected"] == [{"address": "2001:db8::10", "family": "ipv6", "interface": "eth0", "prefix_length": 64}]


def test_import_rejects_unbound_address(inventory):
    with pytest.raises(ValidationError, match="not currently configured"):
        ipmanager.import_addresses({"addresses": ["203.0.113.99"]})


def test_dedicated_address_allows_only_one_account(inventory, monkeypatch):
    monkeypatch.setattr(ipmanager, "_sync_account_dns", lambda *args: (0, []))
    second = next(row for row in inventory["ips"] if row["address"] == "198.51.100.11")
    ipmanager.update_ip({"id": second["id"], "allocation_mode": "dedicated", "label": "VIP", "active": True})
    first = ipmanager.assign_account({"username": "alpha", "selection": "specific", "server_ip_id": second["id"]})
    assert first["address"] == "198.51.100.11"
    with pytest.raises(ValidationError, match="already assigned"):
        ipmanager.assign_account({"username": "beta", "selection": "specific", "server_ip_id": second["id"]})


def test_random_policy_selects_an_active_shared_address(inventory, monkeypatch):
    monkeypatch.setattr(ipmanager, "_sync_account_dns", lambda *args: (0, []))
    monkeypatch.setattr(ipmanager.secrets, "choice", lambda rows: rows[-1])
    result = ipmanager.set_policy({"allocation_policy": "random_shared"})
    assert result["allocation_policy"] == "random_shared"
    assigned = ipmanager.assign_for_new_account({"username": "alpha", "selection": "automatic"})
    assert assigned["address"] in {"198.51.100.10", "198.51.100.11"}


def test_specific_default_must_be_shared(inventory):
    second = next(row for row in inventory["ips"] if row["address"] == "198.51.100.11")
    ipmanager.update_ip({"id": second["id"], "allocation_mode": "dedicated", "active": True})
    with pytest.raises(ValidationError, match="must be a shared IP"):
        ipmanager.set_policy({"allocation_policy": "specific", "default_server_ip_id": second["id"]})


def test_reclassifying_shared_with_two_accounts_is_blocked(inventory, monkeypatch):
    monkeypatch.setattr(ipmanager, "_sync_account_dns", lambda *args: (0, []))
    first = inventory["ips"][0]
    for username in ("alpha", "beta"):
        ipmanager.assign_account({"username": username, "selection": "specific", "server_ip_id": first["id"]})
    with pytest.raises(ValidationError, match="extra account assignments"):
        ipmanager.update_ip({"id": first["id"], "allocation_mode": "dedicated", "active": True})


def test_assignment_updates_account_managed_dns(inventory, monkeypatch):
    first, second = inventory["ips"][:2]
    with write_session() as session:
        account = session.query(Account).filter_by(username="alpha").one()
        session.add(Domain(account_id=account.id, domain="alpha.example", kind="primary", docroot="/home/alpha/public_html"))
        session.add(Domain(account_id=account.id, domain="blog.alpha.example", kind="subdomain", docroot="/home/alpha/blog/public_html"))
        session.add(DnsZone(account_id=account.id, zone="alpha.example"))
    calls = []
    from daemon import dnsprovider
    monkeypatch.setattr(dnsprovider, "upsert_record", lambda zone, label, rtype, values: calls.append((zone, label, rtype, values)))
    result = ipmanager.assign_account({"username": "alpha", "selection": "specific", "server_ip_id": second["id"]})
    assert result["dns_updates"] == 2
    assert ("alpha.example", "@", "A", [second["address"]]) in calls
    assert ("alpha.example", "blog", "A", [second["address"]]) in calls


def test_termination_releases_assignment(inventory, monkeypatch):
    monkeypatch.setattr(ipmanager, "_sync_account_dns", lambda *args: (0, []))
    ipmanager.assign_account({"username": "alpha", "selection": "specific", "server_ip_id": inventory["ips"][0]["id"]})
    with write_session() as session:
        account = session.query(Account).filter_by(username="alpha").one()
    ipmanager.release_account(account)
    with write_session() as session:
        assert session.query(ServerIpAssignment).count() == 0


def test_remove_refuses_assigned_address(inventory, monkeypatch):
    monkeypatch.setattr(ipmanager, "_sync_account_dns", lambda *args: (0, []))
    target = inventory["ips"][0]
    ipmanager.assign_account({"username": "alpha", "selection": "specific", "server_ip_id": target["id"]})
    with pytest.raises(ValidationError, match="assignments"):
        ipmanager.delete_ip({"id": target["id"]})
