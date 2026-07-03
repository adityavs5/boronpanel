import pytest

from daemon import nameservers as ns
from shared.validation import ValidationError


@pytest.fixture()
def fake_powerdns(monkeypatch):
    """In-memory stand-in for PowerDNS's zone/record state, keyed the same
    way daemon/powerdns.py's own list_records/upsert_record already
    shape their return values -- matches the established
    monkeypatch.setattr(<module>.powerdns, ...) pattern used elsewhere in
    this suite (tests/test_handlers_domain.py, tests/test_dkim.py)."""
    zones: dict[str, dict] = {}

    def zone_exists(zone):
        return zone in zones

    def upsert_record(zone, subdomain, rtype, values, ttl=3600):
        name = zone if subdomain in ("@", "") else f"{subdomain}.{zone}"
        zones.setdefault(zone, {})[(name, rtype)] = list(values)

    def list_records(zone):
        records = []
        for (name, rtype), values in zones.get(zone, {}).items():
            records.append({"name": name, "type": rtype, "ttl": 3600, "values": values})
        return records

    monkeypatch.setattr(ns.powerdns, "zone_exists", zone_exists)
    monkeypatch.setattr(ns.powerdns, "upsert_record", upsert_record)
    monkeypatch.setattr(ns.powerdns, "list_records", list_records)
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
