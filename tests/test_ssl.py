import datetime as dt

import pytest

from daemon import handlers_account as ha
from daemon import handlers_domain as hd
from daemon import ssl as fssl
from shared.db import write_session
from shared.models import DnsZone


@pytest.fixture()
def stub_sysops(monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)


@pytest.fixture()
def stub_filesystem(monkeypatch):
    monkeypatch.setattr(hd, "ensure_docroot", lambda username, docroot: None)
    monkeypatch.setattr(hd.ols, "provision_vhost", lambda account: None)


def test_challenge_plan_http01_when_zone_not_managed(isolated_db, stub_sysops, stub_filesystem):
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    mode, args = fssl._challenge_plan("demo1.example")
    assert mode == "http-01"
    assert "--webroot" in args
    assert "/home/demo1/public_html" in args


def test_challenge_plan_dns01_when_zone_managed(isolated_db, stub_sysops, stub_filesystem):
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    with write_session() as session:
        session.add(DnsZone(account_id=1, zone="demo1.example"))

    mode, args = fssl._challenge_plan("demo1.example")
    assert mode == "dns-01"
    assert "--authenticator" in args
    assert "dns-powerdns" in args


def test_challenge_plan_unprovisioned_domain_raises(isolated_db, stub_sysops, stub_filesystem):
    with pytest.raises(fssl.SslError):
        fssl._challenge_plan("nope.example")


def test_issue_certificate_requires_email(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    monkeypatch.setattr(fssl.settings, "letsencrypt_email", "")
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    with pytest.raises(fssl.SslError):
        fssl.issue_certificate({"domain": "demo1.example"})


def test_issue_certificate_calls_certbot_with_expected_args(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    monkeypatch.setattr(fssl.settings, "letsencrypt_email", "ops@example.com")
    captured = {}

    def fake_run(args, timeout=180):
        captured["args"] = args
        from daemon.procutil import ProcResult

        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(fssl, "run", fake_run)

    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    result = fssl.issue_certificate({"domain": "demo1.example"})
    assert result["status"] == "issued"
    assert "--non-interactive" in captured["args"]
    assert "-d" in captured["args"]
    assert "demo1.example" in captured["args"]
    assert "--deploy-hook" in captured["args"]


def test_issue_certificate_raises_on_certbot_failure(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    monkeypatch.setattr(fssl.settings, "letsencrypt_email", "ops@example.com")

    def fake_run(args, timeout=180):
        from daemon.procutil import ProcResult

        return ProcResult(args=args, returncode=1, stdout="", stderr="DNS problem: NXDOMAIN")

    monkeypatch.setattr(fssl, "run", fake_run)

    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    with pytest.raises(fssl.SslError):
        fssl.issue_certificate({"domain": "demo1.example"})

def test_terminate_account_certs_skips_domains_without_active_ssl(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    calls = []
    monkeypatch.setattr(fssl, "run", lambda args, timeout=30: calls.append(args))

    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})  # ssl_status stays "none"

    from shared.models import Account
    with write_session() as session:
        account = session.get(Account, 1)
        fssl.terminate_account_certs(account)

    assert calls == []


def test_terminate_account_certs_deletes_active_cert(isolated_db, stub_sysops, stub_filesystem, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(fssl, "run", lambda args, timeout=30: calls.append(args))
    monkeypatch.setattr(fssl.Path, "exists", lambda self: True)

    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    from shared.models import Account, Domain
    with write_session() as session:
        domain_row = session.scalar(fssl.select(Domain).where(Domain.domain == "demo1.example"))
        domain_row.ssl_status = "active"

    with write_session() as session:
        account = session.get(Account, 1)
        fssl.terminate_account_certs(account)

    assert len(calls) == 1
    assert "delete" in calls[0]
    assert "demo1.example" in calls[0]


def test_challenge_plan_webmail_hostname_uses_http01_with_webmail_docroot(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    monkeypatch.setattr(fssl.settings, "webmail_hostname", "webmail.example.com")
    monkeypatch.setattr(fssl.settings, "webmail_docroot", "/var/lib/roundcube/public_html")

    mode, args = fssl._challenge_plan("webmail.example.com")
    assert mode == "http-01"
    assert "--webroot" in args
    assert "/var/lib/roundcube/public_html" in args


def test_issue_certificate_force_adds_force_renewal_flag(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    monkeypatch.setattr(fssl.settings, "letsencrypt_email", "ops@example.com")
    captured = {}

    def fake_run(args, timeout=180):
        captured["args"] = args
        from daemon.procutil import ProcResult

        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(fssl, "run", fake_run)
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    fssl.issue_certificate({"domain": "demo1.example", "force": True})
    assert "--force-renewal" in captured["args"]


def test_issue_certificate_without_force_omits_flag(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    monkeypatch.setattr(fssl.settings, "letsencrypt_email", "ops@example.com")
    captured = {}

    def fake_run(args, timeout=180):
        captured["args"] = args
        from daemon.procutil import ProcResult

        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(fssl, "run", fake_run)
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    fssl.issue_certificate({"domain": "demo1.example"})
    assert "--force-renewal" not in captured["args"]


def _generate_self_signed_cert(cert_path, days_valid: int, days_ago_start: int = 0):
    """A real X.509 cert (via the `cryptography` library, same one
    daemon/ssl.py itself uses) with a controllable expiry -- lets
    _cert_file_details' valid/expiring/expired classification be tested
    against genuine parsed certificate data, not a mock return value."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=days_ago_start))
        .not_valid_after(now + dt.timedelta(days=days_valid))
        .sign(key, hashes.SHA256())
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def test_cert_file_details_missing_when_no_file(tmp_path, monkeypatch):
    monkeypatch.setattr(fssl, "letsencrypt_cert_paths", lambda name: (str(tmp_path / "privkey.pem"), str(tmp_path / "fullchain.pem")))
    assert fssl._cert_file_details("demo1.example") is None


def test_cert_file_details_valid(tmp_path, monkeypatch):
    cert_path = tmp_path / "fullchain.pem"
    _generate_self_signed_cert(cert_path, days_valid=60)
    monkeypatch.setattr(fssl, "letsencrypt_cert_paths", lambda name: (str(tmp_path / "privkey.pem"), str(cert_path)))

    details = fssl._cert_file_details("demo1.example")
    assert details["cert_status"] == "valid"
    assert details["issuer"] == "CN=Test CA"
    assert details["days_remaining"] >= 59


def test_cert_file_details_expiring_soon(tmp_path, monkeypatch):
    cert_path = tmp_path / "fullchain.pem"
    _generate_self_signed_cert(cert_path, days_valid=10)
    monkeypatch.setattr(fssl, "letsencrypt_cert_paths", lambda name: (str(tmp_path / "privkey.pem"), str(cert_path)))

    details = fssl._cert_file_details("demo1.example")
    assert details["cert_status"] == "expiring"


def test_cert_file_details_expired(tmp_path, monkeypatch):
    cert_path = tmp_path / "fullchain.pem"
    _generate_self_signed_cert(cert_path, days_valid=-5, days_ago_start=30)
    monkeypatch.setattr(fssl, "letsencrypt_cert_paths", lambda name: (str(tmp_path / "privkey.pem"), str(cert_path)))

    details = fssl._cert_file_details("demo1.example")
    assert details["cert_status"] == "expired"


def test_auto_renew_enabled_checks_renewal_conf(tmp_path, monkeypatch):
    monkeypatch.setattr(fssl, "RENEWAL_CONF_DIR", tmp_path)
    assert fssl._auto_renew_enabled("demo1.example") is False
    (tmp_path / "demo1.example.conf").write_text("# fake renewal conf")
    assert fssl._auto_renew_enabled("demo1.example") is True


def test_get_ssl_dashboard_reports_missing_for_domain_with_no_cert(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    from daemon.procutil import ProcResult

    monkeypatch.setattr(fssl, "letsencrypt_cert_paths", lambda name: ("/nonexistent/privkey.pem", "/nonexistent/fullchain.pem"))
    monkeypatch.setattr(fssl, "run", lambda args, timeout=10: ProcResult(args=args, returncode=0, stdout="inactive\n", stderr=""))

    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    result = fssl.get_ssl_dashboard({"username": "demo1"})
    assert result["domains"][0]["cert_status"] == "missing"
    assert result["domains"][0]["auto_renew"] is False


def test_get_ssl_dashboard_reports_valid_cert_and_auto_renew(isolated_db, stub_sysops, stub_filesystem, monkeypatch, tmp_path):
    cert_path = tmp_path / "fullchain.pem"
    _generate_self_signed_cert(cert_path, days_valid=60)
    monkeypatch.setattr(fssl, "letsencrypt_cert_paths", lambda name: (str(tmp_path / "privkey.pem"), str(cert_path)))
    monkeypatch.setattr(fssl, "RENEWAL_CONF_DIR", tmp_path)
    (tmp_path / "demo1.example.conf").write_text("# fake")

    def fake_run(args, timeout=10):
        from daemon.procutil import ProcResult

        return ProcResult(args=args, returncode=0, stdout="active\n", stderr="")

    monkeypatch.setattr(fssl, "run", fake_run)

    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    result = fssl.get_ssl_dashboard({"username": "demo1"})
    assert result["certbot_timer_active"] is True
    entry = result["domains"][0]
    assert entry["cert_status"] == "valid"
    assert entry["auto_renew"] is True
    assert entry["issuer"] == "CN=Test CA"
