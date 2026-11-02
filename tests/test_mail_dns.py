from daemon import mail_dns
from shared.db import write_session
from shared.models import Account, DkimKey, Domain, MailDomain


def test_default_mail_records_exclude_dmarc_and_unready_dkim(monkeypatch):
    monkeypatch.setattr(mail_dns.settings, "mail_hostname", "mail.host.test")
    monkeypatch.setattr(mail_dns.settings, "server_public_ip", "192.0.2.40")
    rows = mail_dns.default_records("example.test", include_dkim=False)
    assert {(row.label, row.rtype) for row in rows} == {
        ("@", "MX"), ("@", "TXT"), ("ftp", "CNAME"), ("mail", "CNAME"),
    }
    assert all(row.key != "dmarc" for row in rows)


def test_repair_preserves_conflicts_until_explicitly_selected(monkeypatch):
    state = {
        "domain": "example.test", "zone": "example.test", "managed": True,
        "provider": "local", "records": [
            {"key": "mx", "name": "@", "type": "MX", "expected": ["10 mail.host.test."], "actual": ["5 old.test."], "status": "conflicting"},
            {"key": "spf", "name": "@", "type": "TXT", "expected": ['"v=spf1 mx a ~all"'], "actual": [], "status": "missing"},
            {"key": "dmarc", "name": "_dmarc", "type": "TXT", "expected": [], "actual": [], "status": "optional"},
        ],
    }
    writes = []
    monkeypatch.setattr(mail_dns, "preview", lambda domain, **kwargs: state)
    monkeypatch.setattr(mail_dns.dnsprovider, "upsert_record", lambda *args, **kwargs: writes.append(args))
    result = mail_dns.repair("example.test")
    assert result["changed"] == ["spf"]
    assert [item[2] for item in writes] == ["TXT"]
    writes.clear()
    result = mail_dns.repair("example.test", ["mx"])
    assert result["changed"] == ["mx", "spf"]
    assert [item[2] for item in writes] == ["MX", "TXT"]


def test_preview_surfaces_inactive_dkim_for_provisioned_mail_domain(isolated_db, monkeypatch):
    with write_session() as db:
        account = Account(username="mailone", status="active")
        db.add(account)
        db.flush()
        db.add(MailDomain(account_id=account.id, domain="example.test"))
        db.add(DkimKey(domain="example.test", selector="default", signing_active=False))
    monkeypatch.setattr(mail_dns, "find_managed_zone", lambda domain: "example.test")
    monkeypatch.setattr(mail_dns.dnsprovider, "list_records", lambda zone: [])
    monkeypatch.setattr(mail_dns.settings, "mail_hostname", "mail.host.test")
    monkeypatch.setattr(mail_dns.settings, "server_public_ip", "")

    state = mail_dns.preview("example.test")

    dkim_row = next(row for row in state["records"] if row["key"] == "dkim")
    assert dkim_row["name"] == "default._domainkey"
    assert dkim_row["status"] == "inactive"
    assert "activation" in dkim_row["detail"].lower()


def test_preview_surfaces_inactive_dkim_before_first_mailbox(isolated_db, monkeypatch):
    with write_session() as db:
        account = Account(username="hostedone", status="active")
        db.add(account)
        db.flush()
        db.add(Domain(
            account_id=account.id, domain="example.test", kind="primary",
            docroot="/home/hostedone/public_html",
        ))
    monkeypatch.setattr(mail_dns, "find_managed_zone", lambda domain: "example.test")
    monkeypatch.setattr(mail_dns.dnsprovider, "list_records", lambda zone: [])
    monkeypatch.setattr(mail_dns.settings, "mail_hostname", "mail.host.test")
    monkeypatch.setattr(mail_dns.settings, "server_public_ip", "")

    state = mail_dns.preview("example.test")

    dkim_row = next(row for row in state["records"] if row["key"] == "dkim")
    assert dkim_row["name"] == "default._domainkey"
    assert dkim_row["status"] == "inactive"


def test_repair_activates_inactive_dkim_before_writing_records(monkeypatch):
    inactive = {
        "domain": "example.test", "zone": "example.test", "managed": True,
        "provider": "local", "records": [
            {"key": "dkim", "name": "default._domainkey", "type": "TXT", "expected": [], "actual": [], "status": "inactive"},
        ],
    }
    ready = {
        **inactive,
        "records": [
            {"key": "dkim", "name": "default._domainkey", "type": "TXT", "expected": ['"v=DKIM1; p=key"'], "actual": ['"v=DKIM1; p=key"'], "status": "present"},
        ],
    }
    states = iter([inactive, ready, ready])
    activations = []
    monkeypatch.setattr(mail_dns, "preview", lambda domain, **kwargs: next(states))
    monkeypatch.setattr(mail_dns.dkim, "setup_dns_signing", lambda domain: activations.append(domain) or {"signing_active": True})

    result = mail_dns.repair("example.test")

    assert activations == ["example.test"]
    assert result["changed"] == ["dkim"]
    assert result["preview"]["records"][0]["status"] == "present"


def test_dmarc_policy_is_validated_and_written_once(monkeypatch):
    state = {
        "managed": True, "zone": "example.test",
        "records": [{"key": "dmarc", "name": "_dmarc", "type": "TXT"}],
    }
    writes = []
    monkeypatch.setattr(mail_dns, "preview", lambda domain, **kwargs: state)
    monkeypatch.setattr(mail_dns.dnsprovider, "upsert_record", lambda *args, **kwargs: writes.append(args))
    result = mail_dns.set_dmarc("example.test", "quarantine", "reports@example.test", "reject")
    assert "p=quarantine" in result["record"] and "sp=reject" in result["record"]
    assert len(writes) == 1 and writes[0][2] == "TXT"


# --- Round 2: readiness against public DNS when the zone is not authoritative

import pytest  # noqa: E402

from daemon import dnsprovider  # noqa: E402

DKIM_KEY = "v=DKIM1; k=rsa; p=" + "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA" * 9 + "IDAQAB"


def _chunks(text, size):
    return " ".join('"' + text[index:index + size] + '"' for index in range(0, len(text), size))


@pytest.fixture()
def readiness(isolated_db, monkeypatch):
    with write_session() as db:
        account = Account(username="mailone", status="active")
        db.add(account)
        db.flush()
        db.add(MailDomain(account_id=account.id, domain="example.test"))
        db.add(DkimKey(domain="example.test", selector="default", signing_active=True))
    monkeypatch.setattr(mail_dns.settings, "mail_hostname", "mail.host.test")
    monkeypatch.setattr(mail_dns.settings, "server_public_ip", "")
    monkeypatch.setattr(mail_dns, "find_managed_zone", lambda domain: "example.test")
    monkeypatch.setattr(mail_dns.dkim, "dkim_txt_value", lambda domain, selector="default": DKIM_KEY)
    local = {
        ("example.test", "NS"): ["ns1.example.test.", "ns2.example.test."],
        ("example.test", "MX"): ["10 mail.host.test."],
        ("example.test", "TXT"): ['"google-site-verification=abc"', '"v=spf1 mx a ~all"'],
        ("default._domainkey.example.test", "TXT"): [dnsprovider.normalize_txt(f'"{DKIM_KEY}"')],
    }
    monkeypatch.setattr(mail_dns.dnsprovider, "list_records", lambda zone: [
        {"name": name, "type": rtype, "values": values} for (name, rtype), values in local.items()])
    public = {}
    queries = []

    def lookup(name, rtype, deadline=None):
        queries.append((name, rtype))
        return public.get((name, rtype), ("nxdomain", []))

    monkeypatch.setattr(mail_dns.mail_local_delivery, "public_lookup", lookup)
    monkeypatch.setattr(mail_dns.mail_local_delivery, "server_addresses", lambda: {"192.0.2.10"})
    return local, public, queries


def _row(state, key):
    return next(row for row in state["records"] if row["key"] == key)


def test_authoritative_zone_keeps_the_local_check(readiness):
    _local, public, _queries = readiness
    public.update({
        ("example.test", "NS"): ("ok", ["ns1.example.test.", "ns2.example.test."]),
        ("ns1.example.test", "A"): ("ok", ["192.0.2.10"]),
        ("ns2.example.test", "A"): ("ok", ["192.0.2.10"]),
    })
    state = mail_dns.preview("example.test")
    assert state["authoritative"] is True and state["source"] == "local"
    assert {row["key"]: row["status"] for row in state["records"]} == {
        "mx": "present", "spf": "present", "ftp": "missing", "dkim": "present", "dmarc": "optional"}
    assert "local_records" not in state


def test_not_authoritative_zone_reports_public_dns(readiness):
    """Live finding M2: MX, SPF and DKIM showed "present" from the local zone
    while the authoritative Cloudflare zone had none of them."""
    _local, public, _queries = readiness
    public.update({
        ("example.test", "NS"): ("ok", ["ana.ns.cloudflare.com.", "bob.ns.cloudflare.com."]),
        ("example.test", "MX"): ("ok", ["10 mail.example.test."]),
        ("mail.example.test", "A"): ("ok", ["192.0.2.10"]),
        ("example.test", "TXT"): ("noanswer", []),
        ("default._domainkey.example.test", "TXT"): ("ok", [_chunks(DKIM_KEY, 200)]),
        ("_dmarc.example.test", "TXT"): ("ok", ['"v=DMARC1; p=none"']),
    })
    state = mail_dns.preview("example.test")
    assert state["authoritative"] is False and state["source"] == "public"
    assert state["detail"].startswith("This server's zone is not authoritative: public DNS delegates "
                                      "example.test to ana.ns.cloudflare.com, bob.ns.cloudflare.com.")
    assert {row["key"]: row["status"] for row in state["records"]} == {
        "mx": "present", "spf": "missing", "dkim": "present", "dmarc": "present"}
    assert _row(state, "dkim")["fqdn"] == "default._domainkey.example.test"
    assert all(row["source"] == "public" for row in state["records"])
    assert {row["key"]: row["status"] for row in state["local_records"]}["spf"] == "present"


def test_public_mx_elsewhere_and_a_different_dkim_key_are_conflicts(readiness):
    _local, public, _queries = readiness
    public.update({
        ("example.test", "NS"): ("ok", ["ns1.dns-host.example."]),
        ("ns1.dns-host.example", "A"): ("ok", ["198.51.100.1"]),
        ("example.test", "MX"): ("ok", ["10 mx.mail-host.example.", "20 mail.example.test."]),
        ("mx.mail-host.example", "A"): ("ok", ["198.51.100.25"]),
        ("example.test", "TXT"): ("ok", ['"v=spf1 include:_spf.mail-host.example ~all"']),
        ("default._domainkey.example.test", "TXT"): ("ok", ['"v=DKIM1; k=rsa; p=OTHERKEY"']),
    })
    state = mail_dns.preview("example.test")
    mx = _row(state, "mx")
    assert mx["status"] == "conflicting" and "mx.mail-host.example" in mx["detail"]
    assert _row(state, "spf")["status"] == "conflicting"
    assert _row(state, "dkim")["status"] == "conflicting"
    assert _row(state, "dmarc")["status"] == "optional"


def test_vanity_nameservers_that_answer_from_this_server_are_authoritative(readiness):
    _local, public, _queries = readiness
    public.update({
        ("example.test", "NS"): ("ok", ["ns1.brand.example.", "ns2.brand.example."]),
        ("ns1.brand.example", "A"): ("ok", ["192.0.2.10"]),
        ("ns2.brand.example", "AAAA"): ("ok", ["2001:db8::10"]),
    })
    state = mail_dns.preview("example.test")
    assert state["authoritative"] is False  # ns2 does not answer from here
    public[("ns2.brand.example", "A")] = ("ok", ["192.0.2.10"])
    assert mail_dns.preview("example.test")["authoritative"] is True


def test_in_zone_nameservers_must_answer_from_this_server(readiness):
    """A domain moved from another host keeps ns1/ns2.<domain> with glue at
    the old host; matching this zone's default NS names is no proof."""
    _local, public, _queries = readiness
    public.update({
        ("example.test", "NS"): ("ok", ["ns1.example.test.", "ns2.example.test."]),
        ("ns1.example.test", "A"): ("ok", ["198.51.100.53"]),
        ("ns2.example.test", "A"): ("ok", ["198.51.100.54"]),
    })
    state = mail_dns.preview("example.test")
    assert state["authoritative"] is False and state["authority"]["status"] == "elsewhere"


def test_configured_server_nameservers_count_by_name(readiness, monkeypatch):
    _local, public, _queries = readiness
    from daemon import dnssetup
    monkeypatch.setattr(dnssetup, "local_nameservers",
                        lambda zone=None: ["ns1.example.test", "ns2.example.test"])
    public[("example.test", "NS")] = ("ok", ["ns1.example.test.", "ns2.example.test."])
    assert mail_dns.preview("example.test")["authoritative"] is True


def test_unanswered_or_missing_delegation(readiness):
    _local, public, queries = readiness
    public[("example.test", "NS")] = ("error", [])
    state = mail_dns.preview("example.test")
    assert state["authoritative"] is None and state["source"] == "local"
    assert "could not be checked" in state["detail"]
    assert queries == [("example.test", "NS")]  # no further lookups once DNS is down

    public[("example.test", "NS")] = ("nxdomain", [])
    state = mail_dns.preview("example.test")
    assert state["authoritative"] is False
    assert "example.test does not exist in public DNS" in state["detail"]


def test_repair_writes_this_servers_zone_and_keeps_other_txt_values(readiness, monkeypatch):
    local, public, _queries = readiness
    public[("example.test", "NS")] = ("ok", ["ana.ns.cloudflare.com."])
    local[("example.test", "TXT")] = ['"google-site-verification=abc"']
    writes = []
    monkeypatch.setattr(mail_dns.dnsprovider, "upsert_record",
                        lambda zone, name, rtype, values, **kw: writes.append((name, rtype, values)))
    result = mail_dns.repair("example.test")
    assert result["changed"] == ["spf", "ftp"]
    assert ("@", "TXT", ['"google-site-verification=abc"', '"v=spf1 mx a ~all"']) in writes
    assert result["preview"]["source"] == "public"


def test_dkim_template_uses_255_octet_strings(readiness):
    value = next(row for row in mail_dns.default_records("example.test") if row.key == "dkim").values[0]
    strings = dnsprovider.txt_strings(value)
    assert len(strings) > 1 and all(len(item) <= 255 for item in strings)
    assert b"".join(strings).decode() == DKIM_KEY
    assert not hasattr(mail_dns, "_unquote")


def test_unmanaged_domain_reports_public_records(readiness, monkeypatch):
    _local, public, _queries = readiness
    monkeypatch.setattr(mail_dns, "find_managed_zone", lambda domain: None)
    public[("example.test", "MX")] = ("ok", ["10 mail.host.test."])
    public[("mail.host.test", "A")] = ("ok", ["192.0.2.10"])
    state = mail_dns.preview("example.test")
    assert state["managed"] is False and state["authoritative"] is False
    assert _row(state, "mx")["status"] == "present" and state["local_records"] == []
    with pytest.raises(mail_dns.ValidationError, match="not covered"):
        mail_dns.repair("example.test")
