"""docs/PLAN-cloudflare.md Phase 1: zone lifecycle + provider dispatch.

Exercises the pending->active state machine (resync barrier), provider
routing in daemon/dnsprovider.py, compensation on partial zone create,
revert, the terminate hook, and the nameservers-section gating -- all
against the in-memory FakeCF (tests/test_cloudflare.py) plus an in-memory
PowerDNS stand-in, with an isolated SQLite DB.
"""
from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from daemon import cloudflare, cloudflare_ops, dnsprovider, handlers_dns, nameservers, powerdns
from daemon.powerdns import PowerDnsError
from shared.config import settings
from shared.db import write_session
from shared.models import Account, CloudflareZone, DnsZone

from tests.test_cloudflare import FakeCF, _err

ZONE = "example.com"


class FakePDNS:
    """In-memory PowerDNS matching daemon/powerdns.py's surface + shapes."""

    def __init__(self):
        self.zones: dict[str, dict[tuple[str, str], dict]] = {}

    def _name(self, zone, subdomain):
        return zone if subdomain in (None, "", "@") else f"{subdomain}.{zone}"

    def zone_exists(self, zone):
        return zone in self.zones

    def create_zone(self, zone, ns_records):
        self.zones[zone] = {(zone, "NS"): {"ttl": 3600, "values": list(ns_records)}}
        return {"name": zone}

    def delete_zone(self, zone):
        self.zones.pop(zone, None)

    def upsert_record(self, zone, subdomain, rtype, values, ttl=3600):
        if zone not in self.zones:
            raise PowerDnsError(404, f"no zone {zone}")
        self.zones[zone][(self._name(zone, subdomain), rtype)] = {"ttl": ttl, "values": list(values)}

    def delete_record(self, zone, subdomain, rtype):
        if zone not in self.zones:
            raise PowerDnsError(404, f"no zone {zone}")
        self.zones[zone].pop((self._name(zone, subdomain), rtype), None)

    def list_records(self, zone):
        if zone not in self.zones:
            raise PowerDnsError(404, f"no zone {zone}")
        return [
            {"name": name, "type": rtype, "ttl": v["ttl"], "values": v["values"]}
            for (name, rtype), v in self.zones[zone].items()
        ]


@pytest.fixture()
def fake_pdns(monkeypatch):
    fake = FakePDNS()
    for name in ("zone_exists", "create_zone", "delete_zone", "upsert_record", "delete_record", "list_records"):
        monkeypatch.setattr(powerdns, name, getattr(fake, name))
    return fake


@pytest.fixture()
def fake_cf(monkeypatch):
    fake = FakeCF()
    monkeypatch.setattr(cloudflare, "_transport", httpx.MockTransport(fake.handler))
    monkeypatch.setitem(settings.secrets, "CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setattr(settings, "cloudflare_account_id", "acct-123")
    monkeypatch.setattr(cloudflare.time, "sleep", lambda s: None)
    return fake


@pytest.fixture()
def managed_zone(isolated_db, fake_pdns):
    """An account owning a live local zone, the state cf.zone_enable expects."""
    with write_session() as session:
        account = Account(username="cfuser", status="active", primary_domain=ZONE)
        session.add(account)
        session.flush()
        session.add(DnsZone(account_id=account.id, zone=ZONE))
        account_id = account.id
    fake_pdns.create_zone(ZONE, [f"ns1.{ZONE}.", f"ns2.{ZONE}."])
    fake_pdns.upsert_record(ZONE, "@", "A", ["192.0.2.10"])
    fake_pdns.upsert_record(ZONE, "www", "CNAME", [f"{ZONE}."])
    fake_pdns.upsert_record(ZONE, "@", "MX", [f"10 mail.{ZONE}."])
    return account_id


@pytest.fixture()
def emitted_events(monkeypatch):
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        cloudflare_ops.events, "emit", lambda event_type, account, **ctx: events.append((event_type, ctx))
    )
    return events


def _cf_row():
    with write_session() as session:
        row = session.scalar(select(CloudflareZone).where(CloudflareZone.zone == ZONE))
        if row is not None:
            session.expunge(row)
        return row


def _enable():
    return cloudflare_ops.zone_enable({"domain": ZONE})


def _activate():
    return cloudflare_ops.zone_status({"domain": ZONE, "check_now": True})


# --- zone_enable ------------------------------------------------------------


def test_zone_enable_creates_pending_zone_with_ns_pair(managed_zone, fake_cf):
    result = _enable()
    assert result["status"] == "pending"
    assert len(result["name_servers"]) == 2
    row = _cf_row()
    assert row is not None and row.status == "pending"
    assert row.cf_zone_id in fake_cf.zones


def test_zone_enable_seeds_records_but_not_apex_ns(managed_zone, fake_cf):
    _enable()
    zid = _cf_row().cf_zone_id
    recs = fake_cf.records[zid]
    assert {(r["name"], r["type"]) for r in recs} == {
        (ZONE, "A"),
        (f"www.{ZONE}", "CNAME"),
        (ZONE, "MX"),
    }
    mx = next(r for r in recs if r["type"] == "MX")
    assert mx["priority"] == 10 and mx["content"] == f"mail.{ZONE}"
    # Every seeded record is grey-cloud (plan SS1.5).
    assert all(not r.get("proxied") for r in recs)


def test_zone_enable_requires_managed_zone(isolated_db, fake_pdns, fake_cf):
    with pytest.raises(RuntimeError, match="no Boron-managed DNS zone"):
        _enable()


def test_zone_enable_rejects_double_enable(managed_zone, fake_cf):
    _enable()
    with pytest.raises(RuntimeError, match="already on Cloudflare"):
        _enable()


def test_zone_enable_requires_token(managed_zone, fake_cf, monkeypatch):
    monkeypatch.delitem(settings.secrets, "CLOUDFLARE_API_TOKEN")
    with pytest.raises(RuntimeError, match="not configured"):
        _enable()


def test_zone_enable_surfaces_taken_domain_guidance(managed_zone, fake_cf):
    fake_cf.fail_next.append(_err(400, 1061, "zone already exists"))
    with pytest.raises(RuntimeError, match="another Cloudflare account"):
        _enable()
    assert _cf_row() is None


def test_zone_enable_compensates_on_seeding_failure(managed_zone, fake_cf, monkeypatch):
    monkeypatch.setattr(
        cloudflare_ops,
        "_resync_powerdns_to_cloudflare",
        lambda zone, zid: (_ for _ in ()).throw(cloudflare.CloudflareError(500, "boom", None)),
    )
    with pytest.raises(cloudflare.CloudflareError):
        _enable()
    assert fake_cf.zones == {}  # half-created zone deleted again
    assert _cf_row() is None


# --- routing while pending / activation barrier ------------------------------


def test_writes_go_to_powerdns_while_pending(managed_zone, fake_pdns, fake_cf):
    _enable()
    dnsprovider.upsert_record(ZONE, "blog", "A", ["192.0.2.20"])
    assert (f"blog.{ZONE}", "A") in fake_pdns.zones[ZONE]
    zid = _cf_row().cf_zone_id
    assert all(r["name"] != f"blog.{ZONE}" for r in fake_cf.records[zid])
    assert dnsprovider.provider_for_zone(ZONE) == "local"


def test_activation_resyncs_pending_window_edits_then_flips(managed_zone, fake_pdns, fake_cf, emitted_events):
    _enable()
    # Edit made during the pending window lands only in PowerDNS...
    dnsprovider.upsert_record(ZONE, "blog", "A", ["192.0.2.20"])
    fake_cf.zones[_cf_row().cf_zone_id]["status"] = "active"

    result = _activate()
    assert result["activated"] is True
    row = _cf_row()
    assert row.status == "active"
    # ...and converged onto Cloudflare at the activation barrier.
    zid = row.cf_zone_id
    assert any(r["name"] == f"blog.{ZONE}" and r["content"] == "192.0.2.20" for r in fake_cf.records[zid])
    assert fake_cf.zones[zid]["ssl_mode"] == "full"
    assert emitted_events == [("dns.zone_activated", {"zone": ZONE})]
    # Subsequent writes route to Cloudflare, not PowerDNS.
    dnsprovider.upsert_record(ZONE, "shop", "A", ["192.0.2.30"])
    assert any(r["name"] == f"shop.{ZONE}" for r in fake_cf.records[zid])
    assert (f"shop.{ZONE}", "A") not in fake_pdns.zones[ZONE]
    assert dnsprovider.provider_for_zone(ZONE) == "cloudflare"


def test_zone_status_stays_pending_until_cf_says_active(managed_zone, fake_cf, emitted_events):
    _enable()
    result = _activate()
    assert result["status"] == "pending"
    assert result["activated"] is False
    assert emitted_events == []


def test_proxied_forced_false_until_rails_exist(managed_zone, fake_cf):
    _enable()
    zid = _cf_row().cf_zone_id
    fake_cf.zones[zid]["status"] = "active"
    _activate()
    dnsprovider.upsert_record(ZONE, "app", "A", ["192.0.2.40"], proxied=True)
    rec = next(r for r in fake_cf.records[zid] if r["name"] == f"app.{ZONE}")
    assert rec["proxied"] is False  # gate: dnsprovider.proxied_allowed() is False in Phase 1


# --- revert -----------------------------------------------------------------


def test_zone_disable_resyncs_back_and_reverts(managed_zone, fake_pdns, fake_cf):
    _enable()
    zid = _cf_row().cf_zone_id
    fake_cf.zones[zid]["status"] = "active"
    _activate()
    # Edit made while Cloudflare was authoritative:
    dnsprovider.upsert_record(ZONE, "cdn", "CNAME", [f"edge.{ZONE}."])

    result = cloudflare_ops.zone_disable({"domain": ZONE})
    assert result["status"] == "reverted"
    assert result["nameservers"] == [f"ns1.{ZONE}", f"ns2.{ZONE}"]
    assert _cf_row() is None
    assert zid not in fake_cf.zones
    # The Cloudflare-era edit was captured back into PowerDNS.
    assert fake_pdns.zones[ZONE][(f"cdn.{ZONE}", "CNAME")]["values"] == [f"edge.{ZONE}."]
    assert dnsprovider.provider_for_zone(ZONE) == "local"


def test_zone_disable_pending_skips_resync(managed_zone, fake_pdns, fake_cf):
    _enable()
    result = cloudflare_ops.zone_disable({"domain": ZONE})
    assert result["status"] == "reverted"
    assert _cf_row() is None
    assert fake_cf.zones == {}


def test_purge_requires_active_zone(managed_zone, fake_cf):
    _enable()
    with pytest.raises(RuntimeError, match="not active"):
        cloudflare_ops.purge_cache({"domain": ZONE})
    zid = _cf_row().cf_zone_id
    fake_cf.zones[zid]["status"] = "active"
    _activate()
    assert cloudflare_ops.purge_cache({"domain": ZONE})["status"] == "purged"
    assert fake_cf.zones[zid]["purged"] is True


# --- reconcile + terminate ---------------------------------------------------


def test_reconcile_pending_zones_activates(managed_zone, fake_cf, emitted_events):
    _enable()
    fake_cf.zones[_cf_row().cf_zone_id]["status"] = "active"
    assert cloudflare_ops.reconcile_pending_zones() == 1
    assert _cf_row().status == "active"
    assert cloudflare_ops.reconcile_pending_zones() == 0  # nothing left pending


def test_terminate_hook_deletes_cf_zone_and_row(managed_zone, fake_cf):
    _enable()
    with write_session() as session:
        account = session.get(Account, managed_zone)
        session.expunge(account)
    cloudflare_ops.terminate_account_cloudflare(account)
    assert fake_cf.zones == {}
    assert _cf_row() is None


def test_handlers_delete_zone_removes_both_backends(managed_zone, fake_pdns, fake_cf):
    _enable()
    handlers_dns.delete_zone({"domain": ZONE})
    assert fake_cf.zones == {}
    assert ZONE not in fake_pdns.zones
    assert _cf_row() is None
    with write_session() as session:
        assert session.scalar(select(DnsZone).where(DnsZone.zone == ZONE)) is None


# --- surface behavior (handlers_dns / nameservers) ---------------------------


def test_list_records_reports_provider_and_cf_status(managed_zone, fake_cf):
    result = handlers_dns.list_records({"domain": ZONE})
    assert result["managed"] is True
    assert result["provider"] == "local"
    assert "cloudflare" not in result

    _enable()
    result = handlers_dns.list_records({"domain": ZONE})
    assert result["provider"] == "local"  # pending: PowerDNS still authoritative
    assert result["cloudflare"]["status"] == "pending"
    assert len(result["cloudflare"]["name_servers"]) == 2
    assert result["proxy_available"] is False

    fake_cf.zones[_cf_row().cf_zone_id]["status"] = "active"
    _activate()
    result = handlers_dns.list_records({"domain": ZONE})
    assert result["provider"] == "cloudflare"
    assert result["cloudflare"]["status"] == "active"


def test_list_records_reports_unmanaged_subdomain_points_at_parent(managed_zone, fake_cf):
    """A subdomain of a managed zone never gets its own zone (or its own
    Cloudflare toggle) -- it's just a record inside the parent's zone
    (plan's apex-vs-subdomain note). list_records must say so instead of
    letting a raw 404 from the DNS backend bubble up."""
    result = handlers_dns.list_records({"domain": f"blog.{ZONE}"})
    assert result["managed"] is False
    assert result["parent_zone"] == ZONE
    assert result["records"] == []
    assert result["provider"] is None
    assert result["cloudflare"] is None


def test_list_records_reports_unmanaged_with_no_parent_zone(isolated_db, fake_pdns, fake_cf):
    result = handlers_dns.list_records({"domain": "totally-unrelated.net"})
    assert result["managed"] is False
    assert result["parent_zone"] is None


def test_nameservers_list_shows_cf_pair_for_cf_zone(managed_zone, fake_cf):
    _enable()
    result = nameservers.list_nameservers({"domain": ZONE})
    assert result["provider"] == "cloudflare"
    assert result["cloudflare_status"] == "pending"
    assert result["nameservers"] == _cf_row().name_servers


def test_nameservers_set_and_reset_refused_on_cf_zone(managed_zone, fake_cf):
    _enable()
    with pytest.raises(nameservers.NameserverError, match="on Cloudflare"):
        nameservers.set_nameservers({"domain": ZONE, "nameservers": ["ns1.other.net"]})
    with pytest.raises(nameservers.NameserverError, match="on Cloudflare"):
        nameservers.reset_nameservers({"domain": ZONE})


def test_nameservers_local_zone_unchanged(managed_zone):
    result = nameservers.list_nameservers({"domain": ZONE})
    assert result["provider"] == "local"
    assert result["nameservers"] == [f"ns1.{ZONE}", f"ns2.{ZONE}"]
