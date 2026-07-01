import pytest

from daemon import dkim
from shared.config import settings


@pytest.fixture()
def dkim_tmp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "dkim_base_dir", str(tmp_path / "dkim"))
    return tmp_path


def test_generate_keypair_creates_private_key(dkim_tmp_dir):
    path = dkim.generate_keypair("demo1.example")
    assert path.exists()
    assert path.read_text().startswith("-----BEGIN")
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_generate_keypair_is_idempotent(dkim_tmp_dir):
    first = dkim.generate_keypair("demo1.example")
    content_before = first.read_text()
    second = dkim.generate_keypair("demo1.example")
    assert second == first
    assert second.read_text() == content_before  # not rotated on repeat call


def test_dkim_txt_value_has_no_pem_markers(dkim_tmp_dir):
    dkim.generate_keypair("demo1.example")
    value = dkim.dkim_txt_value("demo1.example")
    assert value.startswith("v=DKIM1; k=rsa; p=")
    assert "BEGIN" not in value
    assert "\n" not in value


def test_dkim_record_name(dkim_tmp_dir):
    assert dkim.dkim_record_name("demo1.example") == "default._domainkey.demo1.example"
    assert dkim.dkim_record_name("demo1.example", selector="sel2") == "sel2._domainkey.demo1.example"


def test_setup_dns_signing_without_managed_zone(dkim_tmp_dir, isolated_db, monkeypatch):
    monkeypatch.setattr(dkim, "find_managed_zone", lambda domain: None)
    result = dkim.setup_dns_signing("demo1.example")
    assert result["dns_published"] is False
    assert result["selector"] == "default"
    assert "v=spf1" in result["spf_record_value"]
    assert result["dmarc_record_value"] == "v=DMARC1; p=none; rua=mailto:postmaster@demo1.example"


def test_setup_dns_signing_publishes_when_zone_managed(dkim_tmp_dir, isolated_db, monkeypatch):
    published = []
    monkeypatch.setattr(dkim, "find_managed_zone", lambda domain: "example.com")
    monkeypatch.setattr(dkim, "label_within_zone", lambda domain, zone: "demo1")
    monkeypatch.setattr(dkim.powerdns, "upsert_record", lambda zone, label, rtype, values, **kw: published.append((zone, label, rtype)))

    result = dkim.setup_dns_signing("demo1.example.com")
    assert result["dns_published"] is True
    labels = {label for _, label, _ in published}
    assert "demo1" in labels  # SPF at the subdomain label itself
    assert "default._domainkey.demo1" in labels
    assert "_dmarc.demo1" in labels


def test_setup_dns_signing_reuses_existing_selector(dkim_tmp_dir, isolated_db, monkeypatch):
    monkeypatch.setattr(dkim, "find_managed_zone", lambda domain: None)
    first = dkim.setup_dns_signing("demo1.example")
    second = dkim.setup_dns_signing("demo1.example")
    assert first["selector"] == second["selector"] == "default"


def test_teardown_dns_signing_removes_key_dir(dkim_tmp_dir, isolated_db, monkeypatch):
    monkeypatch.setattr(dkim, "find_managed_zone", lambda domain: None)
    dkim.setup_dns_signing("demo1.example")
    assert dkim._domain_dir("demo1.example").exists()
    dkim.teardown_dns_signing("demo1.example")
    assert not dkim._domain_dir("demo1.example").exists()


def test_teardown_dns_signing_deletes_dns_records_when_managed(dkim_tmp_dir, isolated_db, monkeypatch):
    deleted = []
    monkeypatch.setattr(dkim, "find_managed_zone", lambda domain: "example.com")
    monkeypatch.setattr(dkim, "label_within_zone", lambda domain, zone: "demo1")
    monkeypatch.setattr(dkim.powerdns, "upsert_record", lambda *a, **kw: None)
    monkeypatch.setattr(dkim.powerdns, "delete_record", lambda zone, label, rtype: deleted.append((zone, label, rtype)))

    dkim.setup_dns_signing("demo1.example.com")
    dkim.teardown_dns_signing("demo1.example.com")
    assert ("example.com", "demo1", "TXT") in deleted
    assert ("example.com", "default._domainkey.demo1", "TXT") in deleted
    assert ("example.com", "_dmarc.demo1", "TXT") in deleted
