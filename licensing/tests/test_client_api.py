from datetime import datetime, timedelta, timezone

from boron_license import service
from boron_license.models import Event, License
from boron_license.signing import verify_document
from conftest import call
from fastapi.testclient import TestClient
from sqlalchemy import select


def payload(response, signer):
    assert response.status_code == 200, response.text
    return verify_document(signer.public_key, response.json())


def test_first_activation_binds_the_callers_ip(client, signer, issue, sessions):
    license_id, key = issue()
    doc = payload(call(client, "activate", key), signer)
    assert doc["status"] == "active"
    assert doc["ip"] == doc["bound_ip"] == "203.0.113.10"
    assert doc["tier"] == "pro" and doc["max_accounts"] == 30
    issued = datetime.fromisoformat(doc["issued_at"])
    assert datetime.fromisoformat(doc["valid_until"]) - issued == timedelta(days=14)
    with sessions() as session:
        assert session.get(License, license_id).bound_ip == "203.0.113.10"
        assert service.installations(session, license_id)[0]["hostname"] == "panel.acme.example"


def test_other_ip_gets_a_signed_refusal_and_does_not_rebind(client, signer, issue, sessions):
    license_id, key = issue()
    payload(call(client, "activate", key), signer)
    doc = payload(call(client, "activate", key, ip="198.51.100.7", install_id="install-clone"), signer)
    assert doc["status"] == "ip_mismatch"
    assert doc["valid_until"] == doc["issued_at"]
    with sessions() as session:
        assert session.get(License, license_id).bound_ip == "203.0.113.10"
        assert [row["install_id"] for row in service.installations(session, license_id)] == ["install-0001"]


def test_check_never_binds(client, signer, issue):
    _, key = issue()
    assert payload(call(client, "check", key), signer)["status"] == "not_activated"
    assert payload(call(client, "activate", key), signer)["status"] == "active"
    doc = payload(call(client, "check", key, account_count=12), signer)
    assert doc["status"] == "active"


def test_unknown_and_malformed_keys_are_refused_without_a_document(client, issue, sessions):
    issue()
    unknown = call(client, "activate", "BRN-AAAA-BBBB-CCCC-DDDD")
    assert unknown.status_code == 403 and unknown.json() == {"error": "invalid_license"}
    assert call(client, "activate", "not-a-key").status_code == 403
    with sessions() as session:
        # Refused calls are still audited.
        assert session.scalar(select(Event).where(Event.action == "unknown_key")).ip == "203.0.113.10"


def test_suspended_cancelled_and_expired_licenses(client, signer, issue, sessions):
    license_id, key = issue()
    payload(call(client, "activate", key), signer)
    with sessions() as session:
        service.set_status(session, license_id, "suspended", actor="tests", reason="unpaid")
        session.commit()
    assert payload(call(client, "check", key), signer)["status"] == "suspended"
    with sessions() as session:
        service.set_status(session, license_id, "active", actor="tests")
        service.update(session, license_id, actor="tests",
                       expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        session.commit()
    assert payload(call(client, "check", key), signer)["status"] == "expired"


def test_grace_never_extends_past_the_paid_up_date(client, signer, issue):
    expires = datetime.now(timezone.utc) + timedelta(days=3)
    _, key = issue(expires_at=expires)
    doc = payload(call(client, "activate", key), signer)
    assert datetime.fromisoformat(doc["valid_until"]) <= expires
    assert doc["updates_until"] == doc["expires_at"]


def test_x_real_ip_is_ignored_unless_the_peer_is_the_local_proxy(sessions, signer, issue, client):
    _, key = issue()
    direct = TestClient(client.app, client=("192.0.2.50", 1234))
    doc = payload(call(direct, "activate", key, ip="203.0.113.10"), signer)
    assert doc["bound_ip"] == "192.0.2.50"


def test_ipv4_mapped_addresses_bind_as_ipv4(client, signer, issue):
    _, key = issue()
    doc = payload(call(client, "activate", key, ip="::ffff:203.0.113.10"), signer)
    assert doc["bound_ip"] == "203.0.113.10"


def test_request_validation(client, issue):
    _, key = issue()
    assert call(client, "activate", key, install_id="short").status_code == 422
    assert call(client, "activate", key, unexpected=True).status_code == 422
    assert call(client, "activate", key, account_count=-1).status_code == 422


def test_public_key_endpoint_matches_signer(client, signer):
    body = client.get("/v1/public-key").json()
    assert body["key_id"] == signer.key_id
    assert "BEGIN PUBLIC KEY" in body["public_key"]
