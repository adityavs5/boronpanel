import os
from types import SimpleNamespace

import pytest

from daemon import dkim
from shared.config import settings


@pytest.fixture()
def dkim_tmp_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "dkim_base_dir", str(tmp_path / "dkim"))
    # Key-generation unit tests intentionally cover the pre-install path.
    # A host-level OpenDKIM package must not change their ownership model.
    monkeypatch.setattr(dkim, "_opendkim_gid", lambda: None)
    return tmp_path


def test_generate_keypair_creates_private_key(dkim_tmp_dir):
    path = dkim.generate_keypair("demo1.example")
    assert path.exists()
    assert path.read_text().startswith("-----BEGIN")
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_socket_directory_is_owned_by_opendkim_and_not_group_writable(tmp_path, monkeypatch):
    socket_path = tmp_path / "postfix" / "opendkim" / "opendkim.sock"
    ownership = []
    monkeypatch.setattr(dkim, "SOCKET_PATH", socket_path)
    monkeypatch.setattr(
        dkim.pwd,
        "getpwnam",
        lambda username: SimpleNamespace(pw_uid=123, pw_gid=456) if username == "opendkim" else None,
    )
    monkeypatch.setattr(dkim.os, "chown", lambda path, uid, gid: ownership.append((path, uid, gid)))

    dkim._ensure_socket_directory()

    assert socket_path.parent.is_dir()
    assert ownership == [(socket_path.parent, 123, 456)]
    assert os.stat(socket_path.parent).st_mode & 0o777 == 0o750


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
    monkeypatch.setattr(dkim, "configure_signer", lambda: {"active": True})
    result = dkim.setup_dns_signing("demo1.example")
    assert result["dns_published"] is False
    assert result["selector"] == "default"
    assert "v=spf1" in result["spf_record_value"]
    assert result["signing_active"] is True


def test_setup_dns_signing_publishes_when_zone_managed(dkim_tmp_dir, isolated_db, monkeypatch):
    published = []
    monkeypatch.setattr(dkim, "find_managed_zone", lambda domain: "example.com")
    monkeypatch.setattr(dkim, "label_within_zone", lambda domain, zone: "demo1")
    monkeypatch.setattr(dkim, "configure_signer", lambda: {"active": True})
    monkeypatch.setattr(dkim.dnsprovider, "upsert_record", lambda zone, label, rtype, values, **kw: published.append((zone, label, rtype)))

    result = dkim.setup_dns_signing("demo1.example.com")
    assert result["dns_published"] is True
    labels = {label for _, label, _ in published}
    assert "default._domainkey.demo1" in labels
    assert "demo1" not in labels  # Existing SPF is managed by the conflict-aware mail DNS reconciler.
    assert "_dmarc.demo1" not in labels  # DMARC is an explicit customer choice


def test_setup_dns_signing_reuses_existing_selector(dkim_tmp_dir, isolated_db, monkeypatch):
    monkeypatch.setattr(dkim, "find_managed_zone", lambda domain: None)
    monkeypatch.setattr(dkim, "configure_signer", lambda: {"active": True})
    first = dkim.setup_dns_signing("demo1.example")
    second = dkim.setup_dns_signing("demo1.example")
    assert first["selector"] == second["selector"] == "default"


def test_teardown_dns_signing_removes_key_dir(dkim_tmp_dir, isolated_db, monkeypatch):
    monkeypatch.setattr(dkim, "find_managed_zone", lambda domain: None)
    monkeypatch.setattr(dkim, "configure_signer", lambda: {"active": True})
    dkim.setup_dns_signing("demo1.example")
    assert dkim._domain_dir("demo1.example").exists()
    dkim.teardown_dns_signing("demo1.example")
    assert not dkim._domain_dir("demo1.example").exists()


def test_teardown_dns_signing_deletes_dns_records_when_managed(dkim_tmp_dir, isolated_db, monkeypatch):
    deleted = []
    monkeypatch.setattr(dkim, "find_managed_zone", lambda domain: "example.com")
    monkeypatch.setattr(dkim, "label_within_zone", lambda domain, zone: "demo1")
    monkeypatch.setattr(dkim, "configure_signer", lambda: {"active": True})
    monkeypatch.setattr(dkim.dnsprovider, "upsert_record", lambda *a, **kw: None)
    monkeypatch.setattr(dkim.dnsprovider, "delete_record", lambda zone, label, rtype: deleted.append((zone, label, rtype)))

    dkim.setup_dns_signing("demo1.example.com")
    dkim.teardown_dns_signing("demo1.example.com")
    assert ("example.com", "default._domainkey.demo1", "TXT") in deleted
    assert ("example.com", "demo1", "TXT") not in deleted
    assert ("example.com", "_dmarc.demo1", "TXT") not in deleted


def test_setup_dns_signing_does_not_publish_when_signer_fails(dkim_tmp_dir, isolated_db, monkeypatch):
    published = []
    monkeypatch.setattr(dkim, "find_managed_zone", lambda domain: "example.com")
    monkeypatch.setattr(dkim, "configure_signer", lambda: (_ for _ in ()).throw(dkim.DkimError("offline")))
    monkeypatch.setattr(dkim.dnsprovider, "upsert_record", lambda *args, **kwargs: published.append(args))
    result = dkim.setup_dns_signing("demo1.example.com")
    assert result["signing_active"] is False
    assert result["dns_published"] is False
    assert published == []
