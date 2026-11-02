import fnmatch

import pytest

from daemon import nameservers as ns
from daemon import powerdns
from shared.validation import ValidationError


@pytest.fixture()
def fake_powerdns(isolated_db, monkeypatch):
    """In-memory stand-in for the DNS backend's zone/record state, keyed
    the same way daemon/powerdns.py's own list_records/upsert_record
    already shape their return values. nameservers.py now goes through
    daemon/dnsprovider.py (the Cloudflare dispatch layer), so that's what
    gets patched -- including cloudflare_zone_row, pinned to None ("local
    zone"), which also keeps these unit tests off the database entirely.
    The server-wide reference search (powerdns.search_records) runs over
    every zone in the dict, matching names and content like PowerDNS does."""
    zones: dict[str, dict] = {}

    def search_records(pattern, max_results):
        rows = []
        for zone, rrsets in zones.items():
            for (name, rtype), values in rrsets.items():
                for value in values:
                    if fnmatch.fnmatchcase(name.lower(), pattern.lower()) or \
                            fnmatch.fnmatchcase(value.rstrip(".").lower(), pattern.lower()):
                        rows.append({"object_type": "record", "zone": f"{zone}.", "name": f"{name}.",
                                     "type": rtype, "content": value, "disabled": False})
        return rows[:max_results]

    def zone_exists(zone):
        return zone in zones

    def upsert_record(zone, subdomain, rtype, values, ttl=3600, proxied=False):
        name = zone if subdomain in ("@", "") else f"{subdomain}.{zone}"
        zones.setdefault(zone, {})[(name, rtype)] = list(values)

    def delete_record(zone, subdomain, rtype):
        name = zone if subdomain in ("@", "") else f"{subdomain}.{zone}"
        zones.get(zone, {}).pop((name, rtype), None)

    def list_records(zone):
        records = []
        for (name, rtype), values in zones.get(zone, {}).items():
            records.append({"name": name, "type": rtype, "ttl": 3600, "values": values})
        return records

    monkeypatch.setattr(ns.dnsprovider, "zone_exists", zone_exists)
    monkeypatch.setattr(ns.dnsprovider, "upsert_record", upsert_record)
    monkeypatch.setattr(ns.dnsprovider, "delete_record", delete_record)
    monkeypatch.setattr(ns.dnsprovider, "list_records", list_records)
    monkeypatch.setattr(ns.dnsprovider, "cloudflare_zone_row", lambda zone: None)
    monkeypatch.setattr(ns.powerdns, "search_records", search_records)
    zones["example.com"] = {}
    return zones


def test_set_nameservers_external_no_glue_needed(fake_powerdns):
    result = ns.set_nameservers({"domain": "example.com", "nameservers": ["ns1.otherprovider.com", "ns2.otherprovider.com"]})
    assert result["nameservers"] == ["ns1.otherprovider.com", "ns2.otherprovider.com"]
    assert result["glue"] == {}
    listed = ns.list_nameservers({"domain": "example.com"})
    assert listed["nameservers"] == ["ns1.otherprovider.com", "ns2.otherprovider.com"]


def test_set_nameservers_requires_glue_for_in_zone_ns(fake_powerdns):
    with pytest.raises(ns.NameserverError):
        ns.set_nameservers({"domain": "example.com", "nameservers": ["ns1.example.com"]})


def test_set_nameservers_accepts_glue_for_in_zone_ns(fake_powerdns):
    result = ns.set_nameservers({
        "domain": "example.com",
        "nameservers": ["ns1.example.com", "ns2.example.com"],
        "glue": {"ns1.example.com": "203.0.113.10", "ns2.example.com": "203.0.113.11"},
    })
    assert result["glue"] == {"ns1.example.com": "203.0.113.10", "ns2.example.com": "203.0.113.11"}

    listed = ns.list_nameservers({"domain": "example.com"})
    assert listed["glue"]["ns1.example.com"]["a"] == ["203.0.113.10"]
    assert listed["glue"]["ns2.example.com"]["a"] == ["203.0.113.11"]


def test_set_nameservers_accepts_ipv6_glue(fake_powerdns):
    result = ns.set_nameservers({
        "domain": "example.com",
        "nameservers": ["ns1.example.com"],
        "glue": {"ns1.example.com": "2001:db8::1"},
    })
    assert result["glue"]["ns1.example.com"] == "2001:db8::1"
    listed = ns.list_nameservers({"domain": "example.com"})
    assert listed["glue"]["ns1.example.com"]["aaaa"] == ["2001:db8::1"]


def test_set_nameservers_rejects_malformed_glue_ip(fake_powerdns):
    with pytest.raises(ValidationError):
        ns.set_nameservers({
            "domain": "example.com",
            "nameservers": ["ns1.example.com"],
            "glue": {"ns1.example.com": "not-an-ip"},
        })


def test_set_nameservers_only_requires_glue_for_in_zone_entries(fake_powerdns):
    result = ns.set_nameservers({
        "domain": "example.com",
        "nameservers": ["ns1.example.com", "ns.external.com"],
        "glue": {"ns1.example.com": "203.0.113.10"},
    })
    assert "ns.external.com" not in result["glue"]


def test_set_nameservers_rejects_no_zone(fake_powerdns):
    with pytest.raises(ns.NameserverError):
        ns.set_nameservers({"domain": "no-such-zone.com", "nameservers": ["ns1.somewhere.com"]})


def test_set_nameservers_rejects_empty_list(fake_powerdns):
    with pytest.raises(ValidationError):
        ns.set_nameservers({"domain": "example.com", "nameservers": []})


def test_set_nameservers_rejects_too_many(fake_powerdns):
    with pytest.raises(ValidationError):
        ns.set_nameservers({"domain": "example.com", "nameservers": [f"ns{i}.somewhere.com" for i in range(9)]})


def test_set_nameservers_rejects_malformed_hostname(fake_powerdns):
    with pytest.raises(ValidationError):
        ns.set_nameservers({"domain": "example.com", "nameservers": ["not a valid hostname!"]})


def test_reset_nameservers_restores_defaults(fake_powerdns, monkeypatch):
    monkeypatch.setattr(ns.settings, "server_public_ip", "198.51.100.5")
    ns.set_nameservers({"domain": "example.com", "nameservers": ["ns1.otherprovider.com"]})

    result = ns.reset_nameservers({"domain": "example.com"})
    assert result["nameservers"] == ["ns1.example.com", "ns2.example.com"]
    listed = ns.list_nameservers({"domain": "example.com"})
    assert listed["nameservers"] == ["ns1.example.com", "ns2.example.com"]
    assert listed["glue"]["ns1.example.com"]["a"] == ["198.51.100.5"]


def test_needs_glue_detects_apex_and_subdomain():
    assert ns._needs_glue("example.com", "example.com") is True
    assert ns._needs_glue("example.com", "ns1.example.com") is True
    assert ns._needs_glue("example.com", "ns1.otherdomain.com") is False
    assert ns._needs_glue("example.com", "example.com.evil.com") is False  # not a real suffix match


# --- Live-test regressions: stale glue, shared default delegation ----------


def test_reset_removes_glue_of_replaced_in_zone_nameservers(fake_powerdns, monkeypatch):
    """Reset used to put ns1/ns2 back but leave ns7 A / ns8 AAAA answering."""
    monkeypatch.setattr(ns.settings, "server_public_ip", "198.51.100.5")
    ns.set_nameservers({
        "domain": "example.com",
        "nameservers": ["ns7.example.com", "ns8.example.com"],
        "glue": {"ns7.example.com": "192.0.2.77", "ns8.example.com": "2001:db8::88"},
    })
    result = ns.reset_nameservers({"domain": "example.com"})
    zone = fake_powerdns["example.com"]
    assert ("ns7.example.com", "A") not in zone
    assert ("ns8.example.com", "AAAA") not in zone
    assert sorted(result["removed_glue"]) == ["ns7.example.com A", "ns8.example.com AAAA"]
    assert zone[("ns1.example.com", "A")] == ["198.51.100.5"]
    assert zone[("example.com", "NS")] == ["ns1.example.com.", "ns2.example.com."]


def test_switching_custom_nameservers_drops_old_glue_but_keeps_apex_and_hosted_names(fake_powerdns):
    from shared.db import write_session
    from shared.models import Account, Domain

    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        session.add(Domain(account_id=account.id, domain="www.example.com", kind="subdomain",
                           docroot="/home/demo1/www"))
    fake_powerdns["example.com"][("example.com", "A")] = ["192.0.2.1"]
    ns.set_nameservers({
        "domain": "example.com",
        "nameservers": ["example.com", "www.example.com", "ns7.example.com"],
        "glue": {"example.com": "192.0.2.1", "www.example.com": "192.0.2.2", "ns7.example.com": "192.0.2.77"},
    })
    result = ns.set_nameservers({"domain": "example.com", "nameservers": ["ns1.other.net", "ns2.other.net"]})
    zone = fake_powerdns["example.com"]
    assert result["removed_glue"] == ["ns7.example.com A"]
    assert zone[("example.com", "A")] == ["192.0.2.1"]
    assert zone[("www.example.com", "A")] == ["192.0.2.2"]


def test_reset_restores_the_configured_server_nameservers(fake_powerdns, monkeypatch):
    """Reset must restore what create_zone seeds, not a hard-coded ns1/ns2."""
    from shared.db import write_session
    from shared.models import DnsServerSettings

    monkeypatch.setattr(ns.settings, "server_public_ip", "198.51.100.5")
    with write_session() as session:
        session.add(DnsServerSettings(id=1, mode="local", local_nameservers=["ns1.host.net", "ns2.host.net"]))
    result = ns.reset_nameservers({"domain": "example.com"})
    assert result["nameservers"] == ["ns1.host.net", "ns2.host.net"]
    zone = fake_powerdns["example.com"]
    assert zone[("example.com", "NS")] == ["ns1.host.net.", "ns2.host.net."]
    assert not any(rtype in ("A", "AAAA") for (_name, rtype) in zone)  # external: no glue


def test_missing_glue_is_a_user_error_not_an_internal_failure(fake_powerdns):
    with pytest.raises(ValidationError, match="glue"):
        ns.set_nameservers({"domain": "example.com", "nameservers": ["ns7.example.com"]})


def test_dropped_nameserver_address_still_used_by_other_records_is_kept(fake_powerdns):
    """Glue cannot be told apart from an ordinary A record: a host the zone
    still points at (here the MX target) keeps its address and is reported."""
    ns.set_nameservers({
        "domain": "example.com",
        "nameservers": ["mail.example.com", "ns7.example.com"],
        "glue": {"mail.example.com": "192.0.2.25", "ns7.example.com": "192.0.2.77"},
    })
    fake_powerdns["example.com"][("example.com", "MX")] = ["10 mail.example.com."]
    result = ns.set_nameservers({"domain": "example.com", "nameservers": ["ns1.other.net", "ns2.other.net"]})
    zone = fake_powerdns["example.com"]
    assert zone[("mail.example.com", "A")] == ["192.0.2.25"]
    assert result["removed_glue"] == ["ns7.example.com A"]
    assert result["kept_glue"] == [{"name": "mail.example.com", "types": ["A"],
                                    "reason": "example.com MX points at it"}]


def test_server_nameserver_glue_is_never_removed(fake_powerdns, monkeypatch):
    """ns1.example.com may be the vanity nameserver every other zone on the
    server delegates to; repointing example.com itself must not delete it."""
    monkeypatch.setattr(ns.dnssetup, "local_nameservers",
                        lambda zone=None: ["ns1.example.com", "ns2.example.com"])
    ns.set_nameservers({
        "domain": "example.com",
        "nameservers": ["ns1.example.com", "ns2.example.com"],
        "glue": {"ns1.example.com": "192.0.2.1", "ns2.example.com": "192.0.2.2"},
    })
    result = ns.set_nameservers({"domain": "example.com", "nameservers": ["ns1.other.net", "ns2.other.net"]})
    assert result["removed_glue"] == []
    assert {row["name"] for row in result["kept_glue"]} == {"ns1.example.com", "ns2.example.com"}
    assert fake_powerdns["example.com"][("ns1.example.com", "A")] == ["192.0.2.1"]


# --- Round 2: glue other zones on the server depend on --------------------


def _vanity_nameservers():
    ns.set_nameservers({
        "domain": "example.com",
        "nameservers": ["ns7.example.com", "ns8.example.com"],
        "glue": {"ns7.example.com": "192.0.2.77", "ns8.example.com": "192.0.2.88"},
    })


def test_glue_that_other_zones_on_the_server_use_is_kept(fake_powerdns):
    """A reseller's vanity nameserver lives in the reseller's zone, but the
    customer zones delegated to it (not in the configured server list)
    still need its address when the reseller repoints its own zone."""
    _vanity_nameservers()
    fake_powerdns["customer.net"] = {("customer.net", "NS"): ["ns7.example.com.", "ns2.other.net."],
                                     ("customer.net", "MX"): ["10 ns8.example.com."]}
    result = ns.set_nameservers({"domain": "example.com", "nameservers": ["ns1.other.net", "ns2.other.net"]})
    zone = fake_powerdns["example.com"]
    assert zone[("ns7.example.com", "A")] == ["192.0.2.77"]
    assert zone[("ns8.example.com", "A")] == ["192.0.2.88"]
    assert result["removed_glue"] == []
    # The reason never names another account's zone (customers read it).
    assert result["kept_glue"] == [
        {"name": "ns7.example.com", "types": ["A"],
         "reason": "a NS record in another account's zone on this server points at it"},
        {"name": "ns8.example.com", "types": ["A"],
         "reason": "a MX record in another account's zone on this server points at it"},
    ]


def test_kept_glue_names_the_referencing_zone_only_for_the_same_account(fake_powerdns):
    from shared.db import write_session
    from shared.models import Account, DnsZone

    with write_session() as session:
        owner = Account(username="demo1", uid=5001, gid=5001, status="active")
        other = Account(username="demo2", uid=5002, gid=5002, status="active")
        session.add_all([owner, other])
        session.flush()
        session.add_all([DnsZone(account_id=owner.id, zone="example.com"),
                         DnsZone(account_id=owner.id, zone="mine.net"),
                         DnsZone(account_id=other.id, zone="theirs.net")])
    _vanity_nameservers()
    fake_powerdns["mine.net"] = {("mine.net", "NS"): ["ns7.example.com."]}
    fake_powerdns["theirs.net"] = {("theirs.net", "MX"): ["10 ns8.example.com."]}
    result = ns.set_nameservers({"domain": "example.com", "nameservers": ["ns1.other.net", "ns2.other.net"]})
    assert result["kept_glue"] == [
        {"name": "ns7.example.com", "types": ["A"], "reason": "mine.net NS points at it"},
        {"name": "ns8.example.com", "types": ["A"],
         "reason": "a MX record in another account's zone on this server points at it"},
    ]


def test_a_similar_name_in_another_zone_does_not_keep_glue(fake_powerdns):
    _vanity_nameservers()
    fake_powerdns["customer.net"] = {("customer.net", "NS"): ["xns7.example.com.", "ns2.other.net."]}
    result = ns.set_nameservers({"domain": "example.com", "nameservers": ["ns1.other.net", "ns2.other.net"]})
    assert sorted(result["removed_glue"]) == ["ns7.example.com A", "ns8.example.com A"]


@pytest.mark.parametrize("search", [
    lambda pattern, limit: (_ for _ in ()).throw(powerdns.PowerDnsError(500, "search failed")),
    lambda pattern, limit: [],  # a backend without search support finds nothing, not even the glue
    lambda pattern, limit: [{"object_type": "record", "zone": "example.com.", "name": "ns7.example.com.",
                             "type": "A", "content": "192.0.2.77", "disabled": False}] * limit,  # cut short
])
def test_glue_is_kept_when_the_other_zones_cannot_be_checked(fake_powerdns, monkeypatch, search):
    ns.set_nameservers({"domain": "example.com", "nameservers": ["ns7.example.com"],
                        "glue": {"ns7.example.com": "192.0.2.77"}})
    monkeypatch.setattr(ns.powerdns, "search_records", search)
    result = ns.set_nameservers({"domain": "example.com", "nameservers": ["ns1.other.net", "ns2.other.net"]})
    assert result["removed_glue"] == []
    assert result["kept_glue"] == [{"name": "ns7.example.com", "types": ["A"],
                                    "reason": "the other zones on this server could not all be checked"}]
    assert fake_powerdns["example.com"][("ns7.example.com", "A")] == ["192.0.2.77"]


def test_powerdns_search_queries_every_hosted_zone(monkeypatch):
    import httpx

    seen = {}

    def handler(request):
        seen.update(path=request.url.path, params=dict(request.url.params))
        return httpx.Response(200, json=[
            {"object_type": "zone", "name": "customer.net.", "zone_id": "customer.net."},
            {"object_type": "record", "zone": "customer.net.", "name": "customer.net.", "type": "NS",
             "content": "ns7.example.com.", "disabled": False, "ttl": 3600},
        ])

    monkeypatch.setattr(powerdns, "_client", lambda: httpx.Client(base_url="http://pdns/api/v1",
                                                                transport=httpx.MockTransport(handler)))
    rows = powerdns.search_records("*ns7.example.com", 1000)
    assert seen["path"].endswith("/search-data")
    assert seen["params"] == {"q": "*ns7.example.com", "max": "1000", "object_type": "record"}
    assert [row["type"] for row in rows] == ["NS"]


def test_a_hosted_name_without_its_own_zone_lists_its_parent_instead_of_a_502(fake_powerdns):
    from shared.db import write_session
    from shared.models import Account, DnsZone

    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        session.add(DnsZone(account_id=account.id, zone="example.com"))
    listed = ns.list_nameservers({"domain": "blog.example.com"})
    assert listed == {"domain": "blog.example.com", "provider": None, "managed": False,
                      "parent_zone": "example.com", "nameservers": [], "glue": {}}
    assert ns.list_nameservers({"domain": "example.com"})["managed"] is True


def test_glue_host_that_is_a_cname_is_a_user_error_not_a_502(fake_powerdns):
    fake_powerdns["example.com"][("ns7.example.com", "CNAME")] = ["host.other.net."]
    with pytest.raises(ns.NameserverError, match="CNAME"):
        ns.set_nameservers({"domain": "example.com", "nameservers": ["ns7.example.com"],
                            "glue": {"ns7.example.com": "192.0.2.77"}})
    assert ("ns7.example.com", "A") not in fake_powerdns["example.com"]
