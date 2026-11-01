import json
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from boron_license import config, releases, service
from boron_license.api import create_app
from boron_license.models import Event
from conftest import DOWNLOAD_SECRET, PROXY, call, write_release
from fastapi.testclient import TestClient
from sqlalchemy import select

IP = "203.0.113.10"


def updates(client, key, ip=IP, **extra):
    body = {"license_key": key, "install_id": "install-0001", "channel": "stable"} | extra
    return client.post("/v1/updates", json=body, headers={"X-Real-IP": ip})


@pytest.fixture
def active_key(updates_client, issue):
    def _active(**overrides):
        _, key = issue(**overrides)
        assert call(updates_client, "activate", key).status_code == 200
        return key
    return _active


def test_secure_link_token_matches_the_nginx_documentation_example():
    # From the nginx secure_link docs:
    #   echo -n '2147483647/s/link127.0.0.1 secret' | openssl md5 -binary | openssl base64 | tr +/ -_ | tr -d =
    token = releases.secure_link_token(expires=2147483647, uri="/s/link", client_ip="127.0.0.1", secret="secret")
    assert token == "_e4Nc3iduzkWRm01TBBNYw"
    url = releases.signed_url("https://download.example/", "/s/link", client_ip="127.0.0.1", secret="secret",
                              expires=2147483647)
    assert url == "https://download.example/s/link?md5=_e4Nc3iduzkWRm01TBBNYw&expires=2147483647"


def test_current_release_with_signed_links(updates_client, active_key, releases_dir, sessions):
    write_release(releases_dir, "3.3.0")
    key = active_key()
    before = int(time.time())
    response = updates(updates_client, key, current_version="3.2.9")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["format"] == 1 and body["channel"] == "stable" and body["version"] == "3.3.0"
    assert body["published_at"] == "2026-11-01T18:00:00Z" and body["changelog"].startswith("## 3.3.0")
    assert body["archive_size"] == 123456
    assert set(body["urls"]) == {"archive", "checksum", "signature", "metadata", "metadata_signature"}

    archive = urlsplit(body["urls"]["archive"])
    assert (archive.scheme, archive.netloc) == ("https", "download.boronpanel.com")
    assert archive.path == "/protected/releases/3.3.0/boron-3.3.0.tar.gz"
    query = parse_qs(archive.query)
    expires = int(query["expires"][0])
    assert before + 900 <= expires <= int(time.time()) + 900
    assert query["md5"] == [releases.secure_link_token(expires=expires, uri=archive.path, client_ip=IP,
                                                       secret=DOWNLOAD_SECRET)]
    assert datetime.fromisoformat(body["links_expire_at"]) == datetime.fromtimestamp(expires, timezone.utc)
    assert urlsplit(body["urls"]["metadata_signature"]).path.endswith("/boron-3.3.0.metadata.json.sig")

    with sessions() as session:
        event = session.scalars(select(Event).where(Event.action == "updates")).one()
        assert event.ip == IP and event.detail["result"] == "ok" and event.detail["version"] == "3.3.0"
        assert event.detail["current_version"] == "3.2.9"


def test_links_are_bound_to_the_callers_address(updates_client, active_key, releases_dir):
    write_release(releases_dir, "3.3.0")
    key = active_key()
    url = urlsplit(updates(updates_client, key).json()["urls"]["archive"])
    query = parse_qs(url.query)
    other = releases.secure_link_token(expires=int(query["expires"][0]), uri=url.path, client_ip="198.51.100.1",
                                       secret=DOWNLOAD_SECRET)
    assert query["md5"][0] != other


def test_unknown_and_malformed_keys(updates_client, releases_dir, sessions):
    write_release(releases_dir, "3.3.0")
    for key in ("BRN-AAAA-BBBB-CCCC-DDDD", "garbage"):
        response = updates(updates_client, key)
        assert response.status_code == 403 and response.json() == {"error": "invalid_license"}
    with sessions() as session:
        rows = session.scalars(select(Event).where(Event.action == "unknown_key")).all()
        assert len(rows) == 2 and all(row.detail["endpoint"] == "updates" for row in rows)


def test_licenses_that_are_not_usable(updates_client, issue, active_key, releases_dir, sessions):
    write_release(releases_dir, "3.3.0")

    def refused(key, ip=IP):
        response = updates(updates_client, key, ip=ip)
        assert response.status_code == 403
        assert response.json()["error"] == "license_not_usable"
        return response.json()["reason"]

    _, unbound = issue()
    assert refused(unbound) == "not_activated"
    key = active_key()
    assert refused(key, ip="198.51.100.7") == "ip_mismatch"

    with sessions() as session:
        license_id = service.search(session, query=key)[0].id
        service.set_status(session, license_id, "suspended", actor="tests")
        session.commit()
    assert refused(key) == "suspended"
    with sessions() as session:
        service.set_status(session, license_id, "cancelled", actor="tests")
        session.commit()
    assert refused(key) == "cancelled"
    with sessions() as session:
        service.set_status(session, license_id, "active", actor="tests")
        service.update(session, license_id, actor="tests",
                       expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        session.commit()
    assert refused(key) == "expired"

    with sessions() as session:
        results = [row.detail["result"] for row in session.scalars(
            select(Event).where(Event.action == "updates").order_by(Event.id))]
    assert results == ["not_activated", "ip_mismatch", "suspended", "cancelled", "expired"]


def test_future_expiry_is_still_usable(updates_client, active_key, releases_dir):
    write_release(releases_dir, "3.3.0")
    key = active_key(expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    assert updates(updates_client, key).status_code == 200


def test_channel_rules(updates_client, active_key, releases_dir):
    write_release(releases_dir, "3.3.0")
    write_release(releases_dir, "3.4.0", channel="beta")
    stable = active_key()
    response = updates(updates_client, stable, channel="beta")
    assert response.status_code == 403 and response.json() == {"error": "channel_not_allowed"}
    # Naming a beta build explicitly does not get around the rule.
    response = updates(updates_client, stable, version="3.4.0")
    assert response.status_code == 403 and response.json() == {"error": "channel_not_allowed"}

    beta = active_key(channel="beta", customer_email="beta@acme.example")
    assert updates(updates_client, beta, channel="beta").json()["version"] == "3.4.0"
    assert updates(updates_client, beta, channel="stable").json()["version"] == "3.3.0"
    # A beta client may pin a stable build; a stable request may not name a beta one.
    assert updates(updates_client, beta, channel="beta", version="3.3.0").json()["channel"] == "stable"
    assert updates(updates_client, beta, channel="stable", version="3.4.0").status_code == 403


def test_exact_version(updates_client, active_key, releases_dir):
    write_release(releases_dir, "3.3.0")
    write_release(releases_dir, "3.3.1")
    key = active_key()
    body = updates(updates_client, key, version="3.3.0").json()
    assert body["version"] == "3.3.0"
    assert urlsplit(body["urls"]["checksum"]).path == "/protected/releases/3.3.0/boron-3.3.0.sha256"
    assert updates(updates_client, key).json()["version"] == "3.3.1"
    missing = updates(updates_client, key, version="9.9.9")
    assert missing.status_code == 404 and missing.json() == {"error": "no_release"}


def test_no_release_published(updates_client, active_key, sessions):
    key = active_key()
    response = updates(updates_client, key)
    assert response.status_code == 404 and response.json() == {"error": "no_release"}
    with sessions() as session:
        event = session.scalars(select(Event).where(Event.action == "updates")).one()
        assert event.detail["result"] == "no_release" and "stable.json" in event.detail["note"]


@pytest.mark.parametrize("overrides", [
    {"format": 2},
    {"format": True},
    {"channel": "nightly"},
    {"version": "3.3"},
    {"version": "../3.3.0"},
    {"published_at": 5},
    {"changelog": None},
    {"sizes": {"archive": "123"}},
    {"sizes": {"archive": 0}},
    {"sizes": {}},
    {"files": {"archive": "boron-3.3.0.tar.gz"}},
    {"files": {"archive": "../../etc/passwd", "checksum": "boron-3.3.0.sha256",
               "signature": "boron-3.3.0.tar.gz.sig", "metadata": "boron-3.3.0.metadata.json",
               "metadata_signature": "boron-3.3.0.metadata.json.sig"}},
    {"files": {"archive": "boron-3.3.0/../x.tar.gz", "checksum": "boron-3.3.0.sha256",
               "signature": "boron-3.3.0.tar.gz.sig", "metadata": "boron-3.3.0.metadata.json",
               "metadata_signature": "boron-3.3.0.metadata.json.sig"}},
    {"files": {"archive": "boron-3.2.0.tar.gz", "checksum": "boron-3.3.0.sha256",
               "signature": "boron-3.3.0.tar.gz.sig", "metadata": "boron-3.3.0.metadata.json",
               "metadata_signature": "boron-3.3.0.metadata.json.sig"}},
    {"files": {"archive": "boron-3.3.0..tar.gz", "checksum": "boron-3.3.0.sha256",
               "signature": "boron-3.3.0.tar.gz.sig", "metadata": "boron-3.3.0.metadata.json",
               "metadata_signature": "boron-3.3.0.metadata.json.sig"}},
    {"files": {"archive": "boron-3.3.0.tar.gz", "checksum": "boron-3.3.0.tar.gz",
               "signature": "boron-3.3.0.tar.gz.sig", "metadata": "boron-3.3.0.metadata.json",
               "metadata_signature": "boron-3.3.0.metadata.json.sig"}},
])
def test_malformed_indexes_are_refused(updates_client, active_key, releases_dir, overrides):
    write_release(releases_dir, "3.3.0", overrides=overrides)
    response = updates(updates_client, active_key())
    assert response.status_code == 404 and response.json() == {"error": "no_release"}


def test_unreadable_indexes_are_refused(updates_client, active_key, releases_dir):
    key = active_key()
    channel = releases_dir / "channels" / "stable.json"
    for content in (b"not json", b"[1, 2]", b"\xff\xfe", b"{" + b" " * (releases.MAX_INDEX_BYTES + 1) + b"}"):
        channel.write_bytes(content)
        assert updates(updates_client, key).status_code == 404
    # A channel file describing another channel, or a version file another version.
    write_release(releases_dir, "3.3.0", channel="beta", publish=False)
    channel.write_text((releases_dir / "releases" / "3.3.0" / "release.json").read_text())
    assert updates(updates_client, key).status_code == 404
    write_release(releases_dir, "3.3.1")
    (releases_dir / "releases" / "3.3.0" / "release.json").write_text(
        (releases_dir / "releases" / "3.3.1" / "release.json").read_text())
    assert updates(updates_client, key, version="3.3.0").status_code == 404


def test_request_validation(updates_client, active_key, releases_dir):
    write_release(releases_dir, "3.3.0")
    key = active_key()
    for extra in ({"channel": "nightly"}, {"version": "../../x"}, {"version": "3.3"}, {"version": "3.3.0-rc1"},
                  {"install_id": "short"}, {"current_version": "bad version"}, {"unexpected": 1}):
        assert updates(updates_client, key, **extra).status_code == 422, extra


def test_missing_secret_disables_updates(sessions, signer, issue, releases_dir):
    write_release(releases_dir, "3.3.0")
    settings = config.Settings(database_url="unused", releases_dir=releases_dir)
    client = TestClient(create_app(settings, sessions, signer), client=PROXY)
    _, key = issue()
    call(client, "activate", key)
    response = updates(client, key)
    assert response.status_code == 503 and response.json() == {"error": "updates_unavailable"}


def test_x_real_ip_only_from_the_local_proxy(updates_client, active_key, releases_dir):
    write_release(releases_dir, "3.3.0")
    key = active_key()
    direct = TestClient(updates_client.app, client=("192.0.2.50", 1234))
    response = updates(direct, key, ip=IP)
    assert response.status_code == 403 and response.json()["reason"] == "ip_mismatch"


def test_settings_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("BORON_LICENSE_DOWNLOAD_SECRET", " s3cret ")
    monkeypatch.setenv("BORON_LICENSE_RELEASES_DIR", str(tmp_path))
    settings = config.load()
    assert settings.download_secret == "s3cret" and settings.releases_dir == tmp_path
    assert settings.link_lifetime == 900 and "s3cret" not in repr(settings)
    monkeypatch.delenv("BORON_LICENSE_DOWNLOAD_SECRET")
    monkeypatch.delenv("BORON_LICENSE_RELEASES_DIR")
    assert config.load().download_secret == "" and str(config.load().releases_dir) == "/srv/download/protected"


def test_index_reader_rejects_bad_names_directly(releases_dir):
    with pytest.raises(releases.ReleaseNotFound):
        releases.version_index(releases_dir, "../channels/stable")
    with pytest.raises(releases.ReleaseNotFound):
        releases.channel_index(releases_dir, "../releases")
    write_release(releases_dir, "3.3.0")
    assert json.loads((releases_dir / "channels" / "stable.json").read_text())["version"] == "3.3.0"
    assert releases.channel_index(releases_dir, "stable")["files"]["archive"] == "boron-3.3.0.tar.gz"
