"""Panel-side licensing: document verification, state, enforcement, account limit."""
import base64
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException
import pytest

from api.security import Identity, enforce_license
from daemon import license_client
from shared import license as license_state
from shared.config import settings
from shared.validation import ValidationError

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
INSTALL_ID = "0b4f3c1e-5d7a-4c2e-9f00-123456789abc"


@pytest.fixture
def signer():
    key = Ed25519PrivateKey.generate()
    pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    def sign(**overrides):
        payload = {"format": 1, "license_id": 7, "key_hint": "T3MH", "status": "active", "tier": "pro",
                   "max_accounts": 30, "ip": "203.0.113.10", "bound_ip": "203.0.113.10",
                   "install_id": INSTALL_ID, "channel": "stable", "issued_at": NOW.isoformat(),
                   "valid_until": (NOW + timedelta(days=14)).isoformat(), "expires_at": None,
                   "updates_until": None} | overrides
        signature = key.sign(license_state.canonical(payload))
        return {"payload": payload, "signature": base64.b64encode(signature).decode(), "key_id": "test"}
    return SimpleNamespace(sign=sign, pem=pem)


def evaluate(signer, document, now=NOW, install_id=INSTALL_ID):
    return license_state.evaluate(document, install_id=install_id, key_present=True, now=now,
                                  public_key_pem=signer.pem)


def test_fresh_document_is_active(signer):
    state = evaluate(signer, signer.sign())
    assert (state["state"], state["usable"]) == ("active", True)
    assert state["license"]["max_accounts"] == 30


def test_stale_checks_warn_then_lock(signer):
    document = signer.sign()
    assert evaluate(signer, document, now=NOW + timedelta(days=4))["state"] == "warning"
    assert evaluate(signer, document, now=NOW + timedelta(days=4))["usable"] is True
    locked = evaluate(signer, document, now=NOW + timedelta(days=14))
    assert (locked["state"], locked["reason"], locked["usable"]) == ("locked", "check_overdue", False)


def test_expiry_is_reported_as_expired(signer):
    expires = NOW + timedelta(days=2)
    document = signer.sign(expires_at=expires.isoformat(), valid_until=expires.isoformat())
    assert evaluate(signer, document, now=NOW + timedelta(days=3))["reason"] == "expired"


@pytest.mark.parametrize("status", ["suspended", "cancelled", "expired", "ip_mismatch", "not_activated"])
def test_signed_refusals_lock(signer, status):
    state = evaluate(signer, signer.sign(status=status))
    assert (state["usable"], state["reason"]) == (False, status)
    assert state["message"]


def test_tampered_foreign_and_other_install_documents_lock(signer):
    document = signer.sign()
    document["payload"]["max_accounts"] = None
    assert evaluate(signer, document)["reason"] == "invalid_document"
    assert license_state.evaluate(signer.sign(), install_id=INSTALL_ID, key_present=True, now=NOW)[
        "reason"] == "invalid_document"  # not signed by the real license server
    assert evaluate(signer, signer.sign(), install_id="another-install-0001")["reason"] == "install_mismatch"


def test_missing_document():
    no_key = license_state.evaluate(None, install_id=None, key_present=False, now=NOW)
    assert (no_key["state"], no_key["reason"]) == ("unlicensed", "no_license")
    assert license_state.evaluate(None, install_id=None, key_present=True, now=NOW)["reason"] == "not_activated"


def test_normalize_key():
    assert license_state.normalize_key(" brn-abcd-efgh-jkmn-pq23 ") == "BRN-ABCD-EFGH-JKMN-PQ23"
    assert license_state.normalize_key("BRN-ABCD-EFGH-JKMN-PQ20") is None


@pytest.fixture
def enforced(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "license_enforcement", True)
    monkeypatch.setattr(settings, "license_document_path", str(tmp_path / "license.json"))
    monkeypatch.setattr(settings, "license_install_id_path", str(tmp_path / "install-id"))
    monkeypatch.setattr(settings, "license_key_path", str(tmp_path / "license.key"))
    monkeypatch.setattr(settings, "license_state_path", str(tmp_path / "state.json"))
    return tmp_path


def _identity(role="admin", impersonator=None):
    return Identity(panel_user_id=1, username="admin", role=role, account_id=None, auth_method="session",
                    impersonator=impersonator)


def _request(path):
    return SimpleNamespace(url=SimpleNamespace(path=path))


def test_unlicensed_admin_is_limited_to_license_page(enforced):
    with pytest.raises(HTTPException) as refused:
        enforce_license(_identity(), _request("/api/v1/admin/accounts"))
    assert (refused.value.status_code, refused.value.detail) == (402, "license_required")
    with pytest.raises(HTTPException):
        enforce_license(_identity("customer", impersonator="admin"), _request("/api/v1/accounts/x/domains"))
    for path in ("/api/v1/admin/license", "/api/v1/admin/license/state", "/api/v1/whoami", "/logout"):
        enforce_license(_identity(), _request(path))
    # Customers and resellers are never affected.
    enforce_license(_identity("customer"), _request("/api/v1/accounts/x/domains"))
    enforce_license(_identity("reseller"), _request("/api/v1/reseller/accounts"))


def test_enforcement_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "license_enforcement", False)
    enforce_license(_identity(), _request("/api/v1/admin/accounts"))
    assert license_state.account_limit() is None


def test_stored_document_unlocks_and_sets_account_limit(enforced, signer, monkeypatch):
    monkeypatch.setattr(license_state, "LICENSE_PUBLIC_KEY_PEM", signer.pem)
    document = signer.sign(issued_at=datetime.now(timezone.utc).isoformat(),
                           valid_until=(datetime.now(timezone.utc) + timedelta(days=14)).isoformat())
    (enforced / "install-id").write_text(INSTALL_ID + "\n")
    (enforced / "license.json").write_text(json.dumps(document))
    enforce_license(_identity(), _request("/api/v1/admin/accounts"))
    assert license_state.account_limit() == 30

    monkeypatch.setattr(license_client, "_account_count", lambda: 30)
    with pytest.raises(ValidationError, match="allows 30"):
        license_client.require_account_capacity()
    monkeypatch.setattr(license_client, "_account_count", lambda: 29)
    license_client.require_account_capacity()


def test_unusable_license_blocks_new_accounts(enforced):
    with pytest.raises(ValidationError, match="not active"):
        license_client.require_account_capacity()


def test_activate_rejects_malformed_keys_before_any_network_call(enforced, monkeypatch):
    monkeypatch.setattr(license_client.httpx, "post", lambda *a, **k: pytest.fail("network used"))
    with pytest.raises(ValidationError, match="BRN-"):
        license_client.activate({"license_key": "not-a-key"})


def test_refresh_records_unreachable_server_and_keeps_document(enforced, monkeypatch, signer):
    (enforced / "license.key").write_text("BRN-ABCD-EFGH-JKMN-PQ23\n")
    (enforced / "license.json").write_text(json.dumps(signer.sign()))

    def unreachable(*args, **kwargs):
        raise license_client.httpx.ConnectError("down")
    monkeypatch.setattr(license_client.httpx, "post", unreachable)
    monkeypatch.setattr(license_client, "_account_count", lambda: 0)
    result = license_client.refresh()
    assert "unreachable" in result["last_error"]
    assert json.loads((enforced / "license.json").read_text())["payload"]["license_id"] == 7


def test_activate_stores_key_and_document_from_server(enforced, monkeypatch, signer):
    monkeypatch.setattr(license_state, "LICENSE_PUBLIC_KEY_PEM", signer.pem)
    monkeypatch.setattr(license_client, "_account_count", lambda: 3)
    sent = {}

    def fake_post(url, json, timeout, headers):
        sent.update(url=url, body=json)
        document = signer.sign(install_id=json["install_id"])
        return SimpleNamespace(status_code=200, headers={"content-type": "application/json"},
                               json=lambda: document)
    monkeypatch.setattr(license_client.httpx, "post", fake_post)
    license_client.activate({"license_key": "brn-abcd-efgh-jkmn-pq23"})
    assert sent["url"].endswith("/v1/activate") and sent["body"]["account_count"] == 3
    assert (enforced / "license.key").read_text().strip() == "BRN-ABCD-EFGH-JKMN-PQ23"
    assert (enforced / "license.key").stat().st_mode & 0o777 == 0o600
    stored = json.loads((enforced / "license.json").read_text())
    assert stored["payload"]["install_id"] == (enforced / "install-id").read_text().strip()
