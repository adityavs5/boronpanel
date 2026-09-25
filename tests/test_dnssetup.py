from sqlalchemy import select
import pytest

from daemon import dnscluster, dnssetup
from daemon.rpc_authority import authorize, Principal
from shared.db import write_session
from shared.models import Account, CloudflareZone, DnsServerSettings, DnsZone, DnsZoneMigration
from shared.validation import ValidationError


def _account_zone(zone="example.test"):
    with write_session() as db:
        account = Account(username="dnsowner", status="active", primary_domain=zone)
        db.add(account)
        db.flush()
        db.add(DnsZone(account_id=account.id, zone=zone))


def test_mode_change_is_persistent_and_does_not_move_existing_zones(isolated_db):
    _account_zone()
    preview = dnssetup.preview_mode({
        "mode": "local", "local_nameservers": ["ns1.example.net", "ns2.example.net"],
    })
    assert preview["existing_zones_unchanged"] == 1
    assert preview["blockers"] == []
    saved = dnssetup.set_mode({
        "mode": "local", "local_nameservers": ["ns1.example.net", "ns2.example.net"],
    })
    assert saved["mode"] == "local"
    assert saved["zones"][0]["provider"] == "local"
    with write_session() as db:
        assert db.get(DnsServerSettings, 1).local_nameservers == ["ns1.example.net", "ns2.example.net"]


def test_cluster_mode_requires_testable_peer_and_keeps_zone_provider(isolated_db):
    _account_zone()
    blocked = dnssetup.preview_mode({
        "mode": "cluster", "local_nameservers": ["ns1.example.net", "ns2.example.net"],
    })
    assert blocked["blockers"]
    dnscluster.create_peer({
        "name": "dns-secondary", "peer_type": "directadmin",
        "endpoint": "https://dns2.example.test", "username": "admin", "credential": "login-key",
    })
    saved = dnssetup.set_mode({
        "mode": "cluster", "local_nameservers": ["ns1.example.net", "ns2.example.net"],
    })
    assert saved["mode"] == "cluster"
    assert saved["zones"][0]["provider"] == "local"


def test_zone_migration_preview_reports_record_diff(isolated_db, monkeypatch):
    _account_zone()
    records = [{"name": "example.test", "type": "A", "ttl": 300, "values": ["192.0.2.10"]}]
    monkeypatch.setattr(dnssetup.dnsprovider, "list_records", lambda zone: records)
    monkeypatch.setattr(dnssetup.powerdns, "zone_exists", lambda zone: True)
    monkeypatch.setattr(dnssetup.powerdns, "list_records", lambda zone: [])
    preview = dnssetup.preview_zone_migration({"zone": "example.test", "target": "cluster"})
    assert preview["diff"]["create"] == ["example.test A"]
    assert "explicit staged migration" not in preview["switch_condition"]


def test_dnssetup_operations_are_global_admin_only():
    admin = Principal("admin", "admin", None, 1, "session")
    customer = Principal("customer", "client", 2, 2, "session")
    for op in (
        "dnssetup.get", "dnssetup.mode.preview", "dnssetup.mode.set",
        "dnssetup.zone.preview", "dnssetup.zone.migrate", "dnssetup.diagnostics",
    ):
        authorize(op, {}, admin)
        with pytest.raises(Exception):
            authorize(op, {}, customer)


def test_nameserver_validation_rejects_duplicates_and_bad_hostnames(isolated_db):
    with pytest.raises(ValidationError):
        dnssetup.preview_mode({"mode": "local", "local_nameservers": ["ns1.example.net", "ns1.example.net"]})
    with pytest.raises(ValidationError):
        dnssetup.preview_mode({"mode": "local", "local_nameservers": ["bad name", "ns2.example.net"]})


def test_cloudflare_to_local_move_stays_on_cloudflare_until_delegation_verified(isolated_db, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace

    _account_zone()
    with write_session() as db:
        account_id = db.scalar(select(Account.id))
        db.add(CloudflareZone(account_id=account_id, zone="example.test", cf_zone_id="cf-zone",
                              status="active", name_servers=["amy.ns.cloudflare.com", "bob.ns.cloudflare.com"]))
    preview = {
        "zone": "example.test", "source": "cloudflare", "target": "local", "already_on_target": False,
        "record_count": 2, "diff": {"create": [], "update": [], "remove": []}, "warnings": [],
        "switch_condition": "verified",
    }
    monkeypatch.setattr(dnssetup, "preview_zone_migration", lambda params: preview)
    monkeypatch.setattr(dnssetup, "local_nameservers", lambda zone=None: ["ns1.example.net", "ns2.example.net"])
    monkeypatch.setattr(dnssetup.cloudflare_ops, "_zone_token", lambda row: "token")
    monkeypatch.setattr(dnssetup.cloudflare, "use_token", lambda token: nullcontext())
    staged = []
    monkeypatch.setattr(dnssetup.cloudflare_ops, "_resync_cloudflare_to_powerdns", lambda zone, zid: staged.append((zone, zid)))
    disabled = []
    monkeypatch.setattr(dnssetup.cloudflare_ops, "zone_disable", lambda params: disabled.append(params) or {"status": "reverted"})

    result = dnssetup.migrate_zone({"zone": "example.test", "target": "local", "confirm": True})
    assert result["state"] == "waiting_for_delegation"
    assert staged == [("example.test", "cf-zone")]
    assert disabled == []

    monkeypatch.setattr(dnssetup, "run", lambda argv, timeout=20: SimpleNamespace(stdout="ns1.example.net.\nns2.example.net.\n"))
    verified = dnssetup.verify_zone_migration({"zone": "example.test"})
    assert verified["ready"] is True and verified["state"] == "completed"
    assert disabled == [{"domain": "example.test", "force": False}]
    with write_session() as db:
        assert db.scalar(select(DnsZoneMigration)).state == "completed"
