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
    monkeypatch.setattr(mail_dns, "preview", lambda domain: state)
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
    monkeypatch.setattr(mail_dns, "preview", lambda domain: next(states))
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
    monkeypatch.setattr(mail_dns, "preview", lambda domain: state)
    monkeypatch.setattr(mail_dns.dnsprovider, "upsert_record", lambda *args, **kwargs: writes.append(args))
    result = mail_dns.set_dmarc("example.test", "quarantine", "reports@example.test", "reject")
    assert "p=quarantine" in result["record"] and "sp=reject" in result["record"]
    assert len(writes) == 1 and writes[0][2] == "TXT"
