from boron_license import service
from boron_license.models import Event
from boron_license.signing import verify_document
from conftest import call
from sqlalchemy import select


def test_admin_endpoints_require_a_valid_token(client, sessions, admin_headers):
    assert client.get("/v1/admin/licenses").status_code == 401
    assert client.get("/v1/admin/licenses", headers={"Authorization": "Bearer blt_wrong"}).status_code == 401
    assert client.get("/v1/admin/licenses", headers={"Authorization": "Basic x"}).status_code == 401
    assert client.get("/v1/admin/licenses", headers=admin_headers).status_code == 200
    with sessions() as session:
        service.revoke_admin_token(session, "tests", actor="tests")
        session.commit()
    assert client.get("/v1/admin/licenses", headers=admin_headers).status_code == 401


def test_issue_returns_the_key_once_and_it_activates(client, signer, admin_headers, sessions):
    created = client.post("/v1/admin/licenses", headers=admin_headers, json={
        "tier": "solo", "customer_name": "Jo Doe", "customer_email": "Jo@Example.com",
        "expires_at": "2030-01-01T00:00:00Z"})
    assert created.status_code == 201, created.text
    key = created.json()["license_key"]
    license_ = created.json()["license"]
    assert license_["customer_email"] == "jo@example.com" and license_["max_accounts"] == 1
    assert key not in client.get(f"/v1/admin/licenses/{license_['id']}", headers=admin_headers).text
    doc = verify_document(signer.public_key, call(client, "activate", key).json())
    assert doc["status"] == "active" and doc["tier"] == "solo"
    with sessions() as session:
        assert session.scalar(select(Event).where(Event.action == "issue")).actor == "token:tests"


def test_suspend_resume_rebind_and_update(client, signer, admin_headers, issue):
    license_id, key = issue()
    call(client, "activate", key)
    base = f"/v1/admin/licenses/{license_id}"
    assert client.post(f"{base}/suspend", headers=admin_headers, json={"reason": "chargeback"}).json()[
        "license"]["status"] == "suspended"
    assert client.post(f"{base}/resume", headers=admin_headers, json={}).json()["license"]["status"] == "active"

    # Customer moved servers: clear the binding and let the new server activate.
    assert client.post(f"{base}/rebind", headers=admin_headers, json={"ip": None}).json()["license"][
        "bound_ip"] is None
    doc = verify_document(signer.public_key, call(client, "activate", key, ip="198.51.100.7").json())
    assert doc["status"] == "active" and doc["bound_ip"] == "198.51.100.7"

    updated = client.patch(base, headers=admin_headers, json={"tier": "unlimited", "channel": "beta"}).json()
    assert updated["license"]["max_accounts"] is None and updated["license"]["channel"] == "beta"
    shown = client.get(base, headers=admin_headers).json()
    assert [row["ip"] for row in shown["installations"]] == ["198.51.100.7"]


def test_admin_errors_are_reported(client, admin_headers):
    assert client.get("/v1/admin/licenses/999", headers=admin_headers).status_code == 404
    bad = client.post("/v1/admin/licenses", headers=admin_headers, json={
        "tier": "gold", "customer_name": "X", "customer_email": "x@example.com"})
    assert bad.status_code == 404 and bad.json() == {"error": "unknown_tier"}
    bad_email = client.post("/v1/admin/licenses", headers=admin_headers, json={
        "tier": "pro", "customer_name": "X", "customer_email": "nope"})
    assert bad_email.json() == {"error": "invalid_customer_email"}


def test_search_finds_by_email_key_ip_and_id(client, admin_headers, issue):
    license_id, key = issue(customer_email="find@me.example")
    call(client, "activate", key, ip="192.0.2.77")
    for query in ("find@me", key, "192.0.2.77", str(license_id), key[-4:]):
        found = client.get("/v1/admin/licenses", headers=admin_headers, params={"q": query}).json()
        assert [row["id"] for row in found] == [license_id], query
