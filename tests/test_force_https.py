"""Per-domain Force HTTPS (uptimeways QA L3): op, route, policy and rules."""
import datetime as dt
import inspect

import pytest
from fastapi import HTTPException

from api.routers import force_https as force_https_router
from api.security import Identity
from daemon import handlers_force_https, ols
from daemon.rpc_authority import AuthorizationError, Principal, authorize
from shared.db import write_session
from shared.models import Account, Domain, DomainForwarding, MaintenanceMode
from shared.validation import NotFoundError, ValidationError


def _certificate(tmp_path, names, *, expired=False):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    start, end = (now - dt.timedelta(days=60), now - dt.timedelta(days=1)) if expired else \
        (now - dt.timedelta(days=1), now + dt.timedelta(days=60))
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])])
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(start).not_valid_after(end)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(name) for name in names]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "fullchain.pem", tmp_path / "privkey.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return str(key_path), str(cert_path)


@pytest.fixture
def site(isolated_db, tmp_path, monkeypatch):
    """Two accounts; owner1 has example.test, owner2 has other.test."""
    from daemon import snapshot_jobs

    monkeypatch.setattr(snapshot_jobs, "lock", _no_lock)
    with write_session() as db:
        owner, peer = Account(username="owner1", status="active"), Account(username="owner2", status="active")
        db.add_all([owner, peer])
        db.flush()
        db.add_all([
            Domain(account_id=owner.id, domain="example.test", docroot="/home/owner1/public_html", ssl_status="active"),
            Domain(account_id=peer.id, domain="other.test", docroot="/home/owner2/public_html"),
        ])
        ids = owner.id, peer.id
    refreshes = []
    monkeypatch.setattr(ols, "refresh_vhost", lambda account: refreshes.append(account.username))

    def install_certificate(names, **kwargs):
        paths = _certificate(tmp_path, names, **kwargs)
        monkeypatch.setattr(ols, "letsencrypt_cert_paths", lambda cert_name: paths)

    return {"ids": ids, "refreshes": refreshes, "certificate": install_certificate}


class _no_lock:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _set(enabled, domain="example.test", username="owner1"):
    return handlers_force_https.set_force_https({"username": username, "domain": domain, "enabled": enabled})


def test_enabling_needs_a_valid_certificate_for_the_domain(site):
    with pytest.raises(ValidationError, match="needs a valid SSL certificate for example.test"):
        _set(True)
    site["certificate"](["example.test"], expired=True)
    with pytest.raises(ValidationError, match="needs a valid SSL certificate"):
        _set(True)
    site["certificate"](["www.example.test"])
    with pytest.raises(ValidationError, match="needs a valid SSL certificate"):
        _set(True)
    assert site["refreshes"] == []
    with write_session() as db:
        assert db.scalar(ols.select(Domain.force_https).where(Domain.domain == "example.test")) is False


def test_enable_renders_redirect_and_reports_uncovered_names(site):
    site["certificate"](["example.test"])
    result = _set(True)
    assert site["refreshes"] == ["owner1"]
    assert result == {
        "domain": "example.test", "enabled": True, "active": True, "certificate_valid": True,
        "covered_hosts": ["example.test"], "uncovered_hosts": ["www.example.test"], "reason": None,
    }
    assert handlers_force_https.get_force_https({"username": "owner1", "domain": "example.test"}) == result
    # Idempotent: no second render for an unchanged value.
    _set(True)
    assert site["refreshes"] == ["owner1"]
    result = _set(False)
    assert result["enabled"] is False and result["active"] is False and result["reason"] is None
    assert site["refreshes"] == ["owner1", "owner1"]


def test_failed_render_restores_the_previous_value(site, monkeypatch):
    site["certificate"](["example.test"])
    calls = []

    def failing_refresh(account):
        calls.append(account.username)
        if len(calls) == 1:
            raise RuntimeError("OLS config transaction failed")

    monkeypatch.setattr(ols, "refresh_vhost", failing_refresh)
    with pytest.raises(RuntimeError):
        _set(True)
    assert calls == ["owner1", "owner1"]
    with write_session() as db:
        assert db.scalar(ols.select(Domain.force_https).where(Domain.domain == "example.test")) is False


def test_failed_restore_render_still_reports_the_original_failure(site, monkeypatch):
    site["certificate"](["example.test"])

    calls = []

    def failing_refresh(account):
        calls.append(account.username)
        raise RuntimeError("OLS config transaction failed" if len(calls) == 1 else "restore render failed")

    monkeypatch.setattr(ols, "refresh_vhost", failing_refresh)
    with pytest.raises(RuntimeError, match="transaction failed"):
        _set(True)
    with write_session() as db:
        assert db.scalar(ols.select(Domain.force_https).where(Domain.domain == "example.test")) is False


def test_status_explains_when_force_https_has_no_effect(site):
    site["certificate"](["example.test"])
    _set(True)
    with write_session() as db:
        db.add(MaintenanceMode(domain="example.test", enabled=True, bypass_token="t" * 32))
    status = handlers_force_https.get_force_https({"username": "owner1", "domain": "example.test"})
    assert status["active"] is False and status["reason"].startswith("Maintenance mode is on")
    with write_session() as db:
        db.add(DomainForwarding(domain="example.test", target_url="https://example.org", status_code=301))
    status = handlers_force_https.get_force_https({"username": "owner1", "domain": "example.test"})
    assert status["reason"].startswith("Domain forwarding redirects every request")
    site["certificate"](["example.test"], expired=True)
    status = handlers_force_https.get_force_https({"username": "owner1", "domain": "example.test"})
    assert status["enabled"] is True and status["active"] is False
    assert status["reason"].startswith("No valid SSL certificate for example.test is installed")


def test_unknown_or_foreign_domain_is_not_found_and_enabled_must_be_boolean(site):
    with pytest.raises(NotFoundError):
        _set(True, domain="other.test")
    with pytest.raises(NotFoundError):
        handlers_force_https.get_force_https({"username": "owner1", "domain": "missing.test"})
    with pytest.raises(ValidationError, match="enabled must be true or false"):
        _set("yes")


def test_force_https_is_stored_off_for_new_and_existing_domains(isolated_db):
    with write_session() as db:
        account = Account(username="owner3", status="active")
        db.add(account)
        db.flush()
        db.add(Domain(account_id=account.id, domain="new.test", docroot="/home/owner3/public_html"))
    with write_session() as db:
        assert db.scalar(ols.select(Domain.force_https).where(Domain.domain == "new.test")) is False


def test_operations_are_registered_with_a_reviewed_policy():
    from daemon.rpc_policy import POLICY_BY_OPERATION
    from daemon.server import OP_TABLE
    from shared.token_policy import READ_ONLY_OPERATIONS

    assert OP_TABLE["force_https.get"] is handlers_force_https.get_force_https
    assert OP_TABLE["force_https.set"] is handlers_force_https.set_force_https
    assert POLICY_BY_OPERATION["force_https.get"] == POLICY_BY_OPERATION["force_https.set"] == "account_and_domain"
    assert "force_https.get" in READ_ONLY_OPERATIONS and "force_https.set" not in READ_ONLY_OPERATIONS


@pytest.mark.parametrize("op", ["force_https.get", "force_https.set"])
def test_policy_allows_the_owner_only(site, op):
    owner_id, _peer_id = site["ids"]
    principal = Principal("customer", "owner1-login", owner_id, 1, "session")
    authorize(op, {"username": "owner1", "domain": "example.test"}, principal)
    for params in ({"username": "owner2", "domain": "other.test"},
                   {"username": "owner1", "domain": "other.test"},
                   {"username": "owner2", "domain": "example.test"}):
        with pytest.raises(AuthorizationError):
            authorize(op, params, principal)


@pytest.mark.parametrize("endpoint", [force_https_router.get_force_https, force_https_router.set_force_https])
def test_foreign_account_route_denies_before_rpc(site, monkeypatch, endpoint):
    def forbidden(*args, **kwargs):
        pytest.fail("unauthorized route reached privileged RPC")

    monkeypatch.setattr(force_https_router, "call_daemon", forbidden)
    values = dict(username="owner2", domain="other.test", body=force_https_router.ForceHttpsBody(enabled=True),
                  identity=Identity(1, "owner1-login", "customer", site["ids"][0], "session"))
    with pytest.raises(HTTPException) as exc:
        endpoint(**{name: values[name] for name in inspect.signature(endpoint).parameters})
    assert exc.value.status_code == 403


def test_route_is_registered_on_the_api_behind_authentication():
    from scripts.security_inventory import inventory_rows

    rows = {row["name"]: row for row in inventory_rows() if row["kind"] == "route"}
    for method in ("GET", "PUT"):
        row = rows[f"{method} /api/v1/accounts/{{username}}/domains/{{domain}}/force-https"]
        assert row["source"] == "api/routers/force_https.py" and row["auth_dependencies"]
