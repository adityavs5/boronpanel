"""Regression tests for the DNS live-test findings: the simple record editor
enforces the advanced editor's policy, invalid values are 400s (never a
PowerDNS 502), long TXT is split into 255-octet strings on every write, and
new/existing zones get a real SOA primary nameserver."""
import dns.rdata
import pytest

from daemon import dnsprovider, dnssetup, handlers_dns as hdns, mail_dns, powerdns
from shared.validation import ValidationError

ZONE = "example.com"
DKIM_VALUE = "v=DKIM1; k=rsa; p=" + "A" * 392  # ~410 octets like a 2048-bit key


@pytest.fixture()
def zone(isolated_db, monkeypatch):
    records: dict[tuple[str, str], dict] = {(ZONE, "NS"): {"ttl": 3600, "values": ["ns1.example.com.", "ns2.example.com."]}}
    writes = []

    def upsert_record(zone_name, sub, rtype, values, ttl=3600, proxied=False):
        name = zone_name if sub == "@" else f"{sub}.{zone_name}"
        writes.append((sub, rtype, list(values), ttl))
        records[(name, rtype)] = {"ttl": ttl, "values": list(values)}

    def delete_record(zone_name, sub, rtype):
        writes.append((sub, rtype, None, None))

    monkeypatch.setattr(hdns.dnsprovider, "zone_exists", lambda zone_name: zone_name == ZONE)
    monkeypatch.setattr(hdns.dnsprovider, "upsert_record", upsert_record)
    monkeypatch.setattr(hdns.dnsprovider, "delete_record", delete_record)
    monkeypatch.setattr(hdns.dnsprovider, "list_records", lambda zone_name: [
        {"name": n, "type": t, "ttl": r["ttl"], "values": r["values"]} for (n, t), r in records.items()
    ])
    return {"records": records, "writes": writes}


def put(**params):
    return hdns.set_record({"domain": ZONE, **params})


# --- apex NS -------------------------------------------------------------


def test_simple_editor_refuses_apex_ns_like_the_advanced_editor(zone):
    with pytest.raises(ValidationError, match="Nameservers page"):
        put(subdomain="@", type="NS", values=["ns.evil.example"])
    with pytest.raises(ValidationError, match="Nameservers page"):
        hdns.delete_record({"domain": ZONE, "subdomain": "@", "type": "NS"})
    assert zone["writes"] == []


def test_sub_zone_delegation_stays_allowed(zone):
    assert put(subdomain="deleg", type="NS", values=["ns1.elsewhere.example"])["values"] == ["ns1.elsewhere.example."]


# --- values that used to reach PowerDNS and return 502 -------------------


@pytest.mark.parametrize("rtype,value,match", [
    ("MX", "99999 mx.example.com", "MX priority"),
    ("SRV", "70000 5 80 t.example.com", "SRV priority"),
    ("SRV", "1 70000 80 t.example.com", "SRV weight"),
    ("TXT", '"a"b"', "quotes"),
    ("MX", "10 1.2.3.4", "IP address"),
    ("CNAME", "192.0.2.1", "IP address"),
])
def test_invalid_values_are_validation_errors_before_any_write(zone, rtype, value, match):
    with pytest.raises(ValidationError, match=match):
        put(subdomain="x", type=rtype, values=[value])
    assert zone["writes"] == []


def test_duplicate_values_are_collapsed(zone):
    assert put(subdomain="dup", type="A", values=["192.0.2.9", "192.0.2.9"])["values"] == ["192.0.2.9"]
    assert put(subdomain="dup6", type="AAAA", values=["2001:DB8::1", "2001:db8::1"])["values"] == ["2001:db8::1"]


@pytest.mark.parametrize("ttl", [59, 2147483648, 1099511627776])
def test_ttl_outside_rfc2181_range_is_rejected(zone, ttl):
    with pytest.raises(ValidationError, match="TTL"):
        put(subdomain="qttl", type="A", values=["192.0.2.1"], ttl=ttl)


def test_txt_control_characters_are_escaped_not_stored_raw(zone):
    assert put(subdomain="qtxtn", type="TXT", values=["line1\nline2"])["values"] == ['"line1\\010line2"']


def test_value_count_per_rrset_is_capped(zone):
    with pytest.raises(ValidationError, match="at most 100"):
        put(subdomain="big", type="A", values=[f"10.0.{i // 250}.{i % 250}" for i in range(101)])


def test_add_mode_respects_the_cap_after_merging(zone):
    zone["records"][(f"big.{ZONE}", "A")] = {"ttl": 3600, "values": [f"10.0.0.{i}" for i in range(100)]}
    with pytest.raises(ValidationError, match="at most 100"):
        put(subdomain="big", type="A", values=["10.0.1.1"], mode="add")


def test_null_mx_is_accepted_alone_only(zone):
    assert put(subdomain="@", type="MX", values=["0 ."])["values"] == ["0 ."]
    with pytest.raises(ValidationError, match="only MX"):
        put(subdomain="@", type="MX", values=["0 .", "10 mail.example.com"])


def test_caa_tags_are_case_insensitive(zone):
    assert put(subdomain="@", type="CAA", values=["0 ISSUE letsencrypt.org"])["values"] == ['0 issue "letsencrypt.org"']


def test_subdomain_without_its_own_zone_points_to_the_parent(zone, monkeypatch):
    monkeypatch.setattr(hdns, "find_managed_zone", lambda name: ZONE)
    with pytest.raises(ValidationError, match="'example.com' zone.*'blog'"):
        hdns.set_record({"domain": f"blog.{ZONE}", "type": "A", "values": ["192.0.2.1"]})
    with pytest.raises(ValidationError, match="'blog'"):
        hdns.delete_record({"domain": f"blog.{ZONE}", "type": "A"})


def test_existing_zone_on_admin_create_is_a_user_error(zone):
    from shared.db import write_session
    from shared.models import Account, DnsZone, Domain

    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        session.add(Domain(account_id=account.id, domain=ZONE, kind="primary", docroot="/home/demo1/public_html"))
        session.add(DnsZone(account_id=account.id, zone=ZONE))
    with pytest.raises(ValidationError, match="already managed"):
        hdns.create_zone({"domain": ZONE, "username": "demo1"})


# --- TXT longer than 255 octets ------------------------------------------


def test_long_txt_is_split_into_255_octet_strings_that_dnspython_parses():
    content = hdns._validate_txt(DKIM_VALUE)
    strings = dnsprovider.txt_strings(content)
    assert [len(s) for s in strings] == [255, len(DKIM_VALUE) - 255]
    assert dnsprovider.txt_text(content) == DKIM_VALUE
    dns.rdata.from_text("IN", "TXT", content)  # what snapshot restore validates with


def test_txt_reader_follows_zone_file_grammar_and_tolerates_legacy_rows():
    assert dnsprovider.txt_strings('abc "d e"') == [b"abc", b"d e"]
    assert dnsprovider.txt_strings('"caf\\195\\169"') == ["café".encode()]
    assert dnsprovider.txt_strings('"line1\nline2"') == [b"line1\nline2"]  # unreadable by dnspython
    assert dnsprovider.normalize_txt('"a" "b"') == '"ab"'
    assert dnsprovider.normalize_txt("") == '""'


def test_powerdns_writes_split_long_txt_from_every_caller(isolated_db, monkeypatch):
    """mail_dns/dkim publish DKIM as one quoted string; dnsprovider is the
    shared write path, so the split happens there for all of them."""
    sent = []
    monkeypatch.setattr(dnsprovider, "cloudflare_zone_row", lambda zone: None)
    monkeypatch.setattr(dnsprovider, "_cluster_notify", lambda zone, action="upsert": None)
    monkeypatch.setattr(powerdns, "upsert_record", lambda zone, sub, rtype, values, ttl=3600: sent.append(values))
    dnsprovider.upsert_record(ZONE, "default._domainkey", "TXT", [mail_dns._quote(DKIM_VALUE)])
    (content,) = sent[0]
    dns.rdata.from_text("IN", "TXT", content)
    assert dnsprovider.txt_text(content) == DKIM_VALUE


def test_cloudflare_writes_join_split_txt_into_one_string(isolated_db, monkeypatch):
    from types import SimpleNamespace
    from daemon import cloudflare, cloudflare_accounts

    sent = []
    monkeypatch.setattr(dnsprovider, "cloudflare_zone_row",
                        lambda zone: SimpleNamespace(status="active", cf_zone_id="z", cf_account_id=None))
    monkeypatch.setattr(cloudflare_accounts, "token_for_id", lambda ident: "token")
    monkeypatch.setattr(cloudflare, "upsert_record", lambda zone, sub, rtype, values, **kw: sent.append(values))
    dnsprovider.upsert_record(ZONE, "default._domainkey", "TXT", [hdns._validate_txt(DKIM_VALUE)])
    assert sent == [[cloudflare._quote_txt(DKIM_VALUE)]]


def test_mail_dns_treats_split_and_single_string_dkim_as_the_same_record():
    split = dnsprovider.normalize_txt(mail_dns._quote(DKIM_VALUE))
    assert mail_dns._canonical_values("TXT", [split]) == mail_dns._canonical_values("TXT", [mail_dns._quote(DKIM_VALUE)])


# --- SOA primary nameserver ----------------------------------------------


def _soa_zone(mname):
    return {"rrsets": [
        {"name": f"{ZONE}.", "type": "SOA", "ttl": 3600,
         "records": [{"content": f"{mname} hostmaster.{ZONE}. 2026110201 10800 3600 604800 3600", "disabled": False}]},
        {"name": f"{ZONE}.", "type": "NS", "ttl": 3600,
         "records": [{"content": "ns1.example.com.", "disabled": False}, {"content": "ns2.example.com.", "disabled": False}]},
        {"name": f"dkim.{ZONE}.", "type": "TXT", "ttl": 3600,
         "records": [{"content": mail_dns._quote(DKIM_VALUE), "disabled": False}]},
        {"name": f"ok.{ZONE}.", "type": "TXT", "ttl": 3600,
         "records": [{"content": '"short"', "disabled": False}]},
    ]}


def test_new_zone_soa_names_its_primary_nameserver(isolated_db, monkeypatch):
    applied = []
    monkeypatch.setattr(powerdns, "create_zone", lambda zone, ns: {"name": zone})
    monkeypatch.setattr(powerdns, "get_zone", lambda zone: _soa_zone("ns1.boron.invalid."))
    monkeypatch.setattr(powerdns, "apply_rrset_changes", lambda zone, changes: applied.extend(changes))
    monkeypatch.setattr(dnsprovider, "_cluster_notify", lambda zone, action="upsert": None)
    dnsprovider.create_zone(ZONE, ["ns1.example.com.", "ns2.example.com."])
    (soa,) = applied
    assert soa["type"] == "SOA"
    assert soa["records"][0]["content"].startswith(f"ns1.example.com. hostmaster.{ZONE}. 2026110201 ")


def test_zone_creation_survives_an_soa_rewrite_failure(isolated_db, monkeypatch):
    monkeypatch.setattr(powerdns, "create_zone", lambda zone, ns: {"name": zone})
    monkeypatch.setattr(powerdns, "get_zone", lambda zone: (_ for _ in ()).throw(powerdns.PowerDnsError(500, "x")))
    monkeypatch.setattr(dnsprovider, "_cluster_notify", lambda zone, action="upsert": None)
    assert dnsprovider.create_zone(ZONE, ["ns1.example.com."]) == {"name": ZONE}


def test_upgrade_reconcile_repairs_placeholder_soa_and_long_txt_only(isolated_db, monkeypatch):
    from shared.db import write_session
    from shared.models import Account, DnsZone

    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        session.add(DnsZone(account_id=account.id, zone=ZONE))
    applied = []
    monkeypatch.setattr(dnsprovider, "cloudflare_zone_row", lambda zone: None)
    monkeypatch.setattr(powerdns, "zone_exists", lambda zone: True)
    monkeypatch.setattr(powerdns, "get_zone", lambda zone: _soa_zone("ns1.boron.invalid."))
    monkeypatch.setattr(powerdns, "apply_rrset_changes", lambda zone, changes: applied.extend(changes))
    monkeypatch.setattr(dnsprovider, "_cluster_notify", lambda zone, action="upsert": None)

    assert dnssetup.reconcile_zone_records() == {"repaired": [ZONE], "failed": [], "skipped": None}
    changed = {row["type"]: row for row in applied}
    assert set(changed) == {"SOA", "TXT"} and changed["TXT"]["name"] == f"dkim.{ZONE}."
    assert changed["SOA"]["records"][0]["content"].split()[0] == "ns1.example.com."
    dns.rdata.from_text("IN", "TXT", changed["TXT"]["records"][0]["content"])

    applied.clear()
    monkeypatch.setattr(powerdns, "get_zone", lambda zone: {"rrsets": _soa_zone("ns1.example.com.")["rrsets"][:2]})
    assert dnssetup.reconcile_zone_records()["repaired"] == [] and applied == []


def test_upgrade_reconcile_never_raises(isolated_db, monkeypatch):
    from shared.db import write_session
    from shared.models import Account, DnsZone

    with write_session() as session:
        account = Account(username="demo1", uid=5001, gid=5001, status="active")
        session.add(account)
        session.flush()
        session.add(DnsZone(account_id=account.id, zone=ZONE))
    monkeypatch.setattr(dnsprovider, "cloudflare_zone_row", lambda zone: None)
    monkeypatch.setattr(powerdns, "zone_exists", lambda zone: (_ for _ in ()).throw(OSError("pdns down")))
    assert dnssetup.reconcile_zone_records()["failed"] == [ZONE]


# --- Readers outside dnsprovider must understand split TXT ------------------


def test_cloudflare_resyncs_translate_split_txt_both_ways(monkeypatch):
    """cf.zone_enable/revert copy rrsets between providers directly; a split
    DKIM sent unchanged would land on Cloudflare as 'a" "b'."""
    from daemon import cloudflare, cloudflare_ops

    split = dnsprovider.normalize_txt('"' + DKIM_VALUE + '"')
    sent = {}
    monkeypatch.setattr(powerdns, "list_records", lambda zone: [
        {"name": f"default._domainkey.{ZONE}", "type": "TXT", "ttl": 3600, "values": [split]}])
    monkeypatch.setattr(cloudflare, "list_records", lambda zone, zone_id=None: [])
    monkeypatch.setattr(cloudflare, "upsert_record",
                        lambda zone, label, rtype, values, **kw: sent.setdefault("cf", values))
    cloudflare_ops._resync_powerdns_to_cloudflare(ZONE, "cfid")
    assert cloudflare._to_cf_payload("TXT", "x", sent["cf"][0], 3600, False)["content"] == DKIM_VALUE

    monkeypatch.setattr(cloudflare, "list_records", lambda zone, zone_id=None: [
        {"name": f"default._domainkey.{ZONE}", "type": "TXT", "ttl": 3600, "values": ['"' + DKIM_VALUE + '"']}])
    monkeypatch.setattr(powerdns, "upsert_record",
                        lambda zone, label, rtype, values, ttl=3600: sent.setdefault("pdns", values))
    monkeypatch.setattr(powerdns, "delete_record", lambda *a: None)
    cloudflare_ops._resync_cloudflare_to_powerdns(ZONE, "cfid")
    dns.rdata.from_text("IN", "TXT", sent["pdns"][0])  # strict zone-file grammar reads it
    assert dnsprovider.txt_text(sent["pdns"][0]) == DKIM_VALUE


def test_cpanel_cluster_peer_gets_the_joined_txt_data():
    from daemon import dnscluster

    split = dnsprovider.normalize_txt('"' + DKIM_VALUE + '"')
    params = dnscluster._cpanel_record_params(ZONE, {"name": "default._domainkey", "type": "TXT", "ttl": 3600}, split)
    assert params["txtdata"] == DKIM_VALUE
