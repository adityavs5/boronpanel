from daemon import mail_dns


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

