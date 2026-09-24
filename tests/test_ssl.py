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
    monkeypatch.setattr(hd, "ensure_docroot", lambda username, docroot, domain_name=None: None)
    monkeypatch.setattr(hd.ols, "provision_vhost", lambda account: None)


def test_challenge_plan_http01_when_zone_not_managed(isolated_db, stub_sysops, stub_filesystem):
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    mode, args = fssl._challenge_plan("demo1.example")
    assert mode == "http-01"
    assert "--manual-auth-hook" in args
    assert "manual" in args
    assert "/home/demo1/public_html" not in args


def test_challenge_plan_dns01_when_zone_managed(isolated_db, stub_sysops, stub_filesystem):
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    with write_session() as session:
        session.add(DnsZone(account_id=1, zone="demo1.example"))

    mode, args = fssl._challenge_plan("demo1.example")
    assert mode == "dns-01"
    assert "--authenticator" in args
    assert "dns-powerdns" in args


def _make_cf_active_zone(tmp_path, monkeypatch, domain="demo1.example"):
    """A CF-active zone (local DnsZone kept as revert target) + a creds path
    inside tmp_path + a token to write into it. Phase 2+3 feature 5."""
    from shared.config import settings
    from shared.models import CloudflareZone

    monkeypatch.setattr(settings, "cloudflare_credentials_file", str(tmp_path / "cf-creds.ini"))
    monkeypatch.setitem(settings.secrets, "CLOUDFLARE_API_TOKEN", "cf-secret-token")
    with write_session() as session:
        session.add(DnsZone(account_id=1, zone=domain))
        session.add(
            CloudflareZone(
                account_id=1, zone=domain, cf_zone_id="z1", status="active", name_servers=[], cf_account_id=None
            )
        )


def test_challenge_plan_uses_dns_cloudflare_when_zone_cf_active(isolated_db, stub_sysops, stub_filesystem, tmp_path, monkeypatch):
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    _make_cf_active_zone(tmp_path, monkeypatch)

    mode, args = fssl._challenge_plan("demo1.example")
    assert mode == "dns-01"
    assert "dns-cloudflare" in args
    assert "dns-powerdns" not in args
    creds = tmp_path / "cf-creds.ini"
    assert creds.exists()
    assert oct(creds.stat().st_mode)[-3:] == "600"  # certbot requires 0600
    assert "cf-secret-token" in creds.read_text()


def test_wildcard_uses_cloudflare_authenticator_when_active(isolated_db, stub_sysops, stub_filesystem, tmp_path, monkeypatch):
    monkeypatch.setattr(fssl.settings, "letsencrypt_email", "ops@example.com")
    captured = {}

    def fake_run(args, timeout=180):
        captured["args"] = args
        from daemon.procutil import ProcResult

        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(fssl, "run", fake_run)
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    _make_cf_active_zone(tmp_path, monkeypatch)

    result = fssl.issue_wildcard_certificate({"domain": "demo1.example"})
    assert result["status"] == "issued"
    assert "dns-cloudflare" in captured["args"]
    assert "*.demo1.example" in captured["args"]
    assert "demo1.example" in captured["args"]


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
    assert "--manual-auth-hook" in args
    assert "manual" in args


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


def test_admin_ssl_dashboard_lists_every_account_without_impersonation(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    from daemon.procutil import ProcResult

    monkeypatch.setattr(fssl.settings, "webmail_hostname", "")
    monkeypatch.setattr(fssl.settings, "pma_hostname", "")
    monkeypatch.setattr(fssl, "_cert_file_details", lambda domain: None)
    monkeypatch.setattr(fssl, "run", lambda args, timeout=10: ProcResult(args=args, returncode=0, stdout="active\n", stderr=""))
    for username, domain in (("alpha", "alpha.example"), ("bravo", "bravo.example")):
        ha.create_account({"username": username})
        hd.add_domain({"username": username, "domain": domain, "kind": "primary"})
    result = fssl.get_admin_ssl_dashboard({})
    assert result["certbot_timer_active"] is True
    assert {(row["username"], row["domain"]) for row in result["domains"]} == {
        ("alpha", "alpha.example"), ("bravo", "bravo.example"),
    }
    assert all(row["cert_status"] == "missing" for row in result["domains"])


# --- Phase 7a feature 5: wildcard SSL via DNS-01 ---------------------------


def test_issue_wildcard_requires_managed_zone(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    monkeypatch.setattr(fssl.settings, "letsencrypt_email", "ops@example.com")
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    with pytest.raises(fssl.SslError, match="requires its DNS zone to be managed"):
        fssl.issue_wildcard_certificate({"domain": "demo1.example"})


def test_issue_wildcard_requires_email(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    monkeypatch.setattr(fssl.settings, "letsencrypt_email", "")
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    with write_session() as session:
        session.add(DnsZone(account_id=1, zone="demo1.example"))

    with pytest.raises(fssl.SslError):
        fssl.issue_wildcard_certificate({"domain": "demo1.example"})


def test_issue_wildcard_rejects_domain_already_wildcard_prefixed(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    monkeypatch.setattr(fssl.settings, "letsencrypt_email", "ops@example.com")
    with pytest.raises(fssl.SslError, match="pass the base domain"):
        fssl.issue_wildcard_certificate({"domain": "*.demo1.example"})


def test_issue_wildcard_calls_certbot_with_both_sans_and_dns01(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    monkeypatch.setattr(fssl.settings, "letsencrypt_email", "ops@example.com")
    captured = {}

    def fake_run(args, timeout=180):
        captured["args"] = args
        from daemon.procutil import ProcResult

        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(fssl, "run", fake_run)

    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    with write_session() as session:
        session.add(DnsZone(account_id=1, zone="demo1.example"))

    result = fssl.issue_wildcard_certificate({"domain": "demo1.example"})
    assert result["status"] == "issued"
    assert result["wildcard_domain"] == "*.demo1.example"
    args = captured["args"]
    assert "demo1.example" in args
    assert "*.demo1.example" in args
    assert "--authenticator" in args
    assert "dns-powerdns" in args
    # never falls back to webroot/http-01 -- wildcard has no HTTP-01 path
    assert "--webroot" not in args


def test_issue_wildcard_raises_on_certbot_failure(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    monkeypatch.setattr(fssl.settings, "letsencrypt_email", "ops@example.com")

    def fake_run(args, timeout=180):
        from daemon.procutil import ProcResult

        return ProcResult(args=args, returncode=1, stdout="", stderr="PowerDNS API error")

    monkeypatch.setattr(fssl, "run", fake_run)

    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    with write_session() as session:
        session.add(DnsZone(account_id=1, zone="demo1.example"))

    with pytest.raises(fssl.SslError):
        fssl.issue_wildcard_certificate({"domain": "demo1.example"})


# --- Phase 7b feature 3: SSL expiry notifications ---------------------------


def _activate_domain_ssl(domain_name: str) -> None:
    from sqlalchemy import select as _select

    from shared.models import Domain

    with write_session() as session:
        d = session.scalar(_select(Domain).where(Domain.domain == domain_name))
        d.ssl_status = "active"


def _stub_cert_paths_per_domain(monkeypatch, tmp_path, cert_paths: dict):
    def fake_paths(domain):
        return str(tmp_path / f"{domain}.key"), str(cert_paths.get(domain, tmp_path / f"{domain}-missing.pem"))

    monkeypatch.setattr(fssl, "letsencrypt_cert_paths", fake_paths)


def _stub_events_emit(monkeypatch, sink: list):
    monkeypatch.setattr(fssl, "events", type("E", (), {"emit": staticmethod(lambda *a, **k: sink.append((a, k)))})())


def test_check_expiring_certificates_sends_for_domain_within_window(isolated_db, stub_sysops, stub_filesystem, tmp_path, monkeypatch):
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    _activate_domain_ssl("demo1.example")

    cert_path = tmp_path / "demo1.example.pem"
    _generate_self_signed_cert(cert_path, days_valid=10)
    _stub_cert_paths_per_domain(monkeypatch, tmp_path, {"demo1.example": cert_path})

    emitted = []
    _stub_events_emit(monkeypatch, emitted)

    sent = fssl.check_expiring_certificates()
    assert sent == 1
    assert emitted[0][0][0] == "ssl.expiring"
    assert emitted[0][1]["domain"] == "demo1.example"


def test_check_expiring_certificates_skips_domain_far_from_expiry(isolated_db, stub_sysops, stub_filesystem, tmp_path, monkeypatch):
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    _activate_domain_ssl("demo1.example")

    cert_path = tmp_path / "demo1.example.pem"
    _generate_self_signed_cert(cert_path, days_valid=60)
    _stub_cert_paths_per_domain(monkeypatch, tmp_path, {"demo1.example": cert_path})

    assert fssl.check_expiring_certificates() == 0


def test_check_expiring_certificates_deduplicates_same_expiry(isolated_db, stub_sysops, stub_filesystem, tmp_path, monkeypatch):
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    _activate_domain_ssl("demo1.example")

    cert_path = tmp_path / "demo1.example.pem"
    _generate_self_signed_cert(cert_path, days_valid=10)
    _stub_cert_paths_per_domain(monkeypatch, tmp_path, {"demo1.example": cert_path})

    emitted = []
    _stub_events_emit(monkeypatch, emitted)

    first = fssl.check_expiring_certificates()
    second = fssl.check_expiring_certificates()
    assert first == 1
    assert second == 0, "must not re-notify for the same certificate's expiry date twice"
    assert len(emitted) == 1


def test_check_expiring_certificates_renewed_cert_notifies_again(isolated_db, stub_sysops, stub_filesystem, tmp_path, monkeypatch):
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    _activate_domain_ssl("demo1.example")

    cert_path = tmp_path / "demo1.example.pem"
    _generate_self_signed_cert(cert_path, days_valid=10)
    _stub_cert_paths_per_domain(monkeypatch, tmp_path, {"demo1.example": cert_path})
    _stub_events_emit(monkeypatch, [])
    assert fssl.check_expiring_certificates() == 1

    # Simulate a renewal: a new cert with a different (still soon) expiry
    # date must be treated as a fresh notice-worthy event, not silently
    # suppressed by the dedup marker from the OLD expiry date.
    _generate_self_signed_cert(cert_path, days_valid=11)
    assert fssl.check_expiring_certificates() == 1


def test_check_expiring_certificates_no_active_domains_returns_zero(isolated_db, stub_sysops, stub_filesystem):
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    assert fssl.check_expiring_certificates() == 0


def test_https_www_wordpress_expands_certificate_names(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    from shared.models import WordPressSiteState
    from daemon.procutil import ProcResult
    monkeypatch.setattr(fssl.settings,'letsencrypt_email','ops@example.com')
    ha.create_account({'username':'demo1'})
    hd.add_domain({'username':'demo1','domain':'demo1.example','kind':'primary'})
    with write_session() as s:
        s.add(WordPressSiteState(account_id=1,domain='demo1.example',path='',site_url='https://www.demo1.example'))
    captured=[]
    def run(args,**kw):
        captured.extend(args)
        return ProcResult(args=args,returncode=0,stdout='',stderr='')
    monkeypatch.setattr(fssl,'run',run)
    fssl.issue_certificate({'domain':'demo1.example'})
    assert '--expand' in captured
    assert captured.count('-d')==2 and 'www.demo1.example' in captured


def test_phpmyadmin_uses_its_own_webroot(monkeypatch):
    from shared.config import settings
    monkeypatch.setattr(settings,'pma_hostname','pma.example.com')
    monkeypatch.setattr(settings,'pma_docroot','/srv/phpmyadmin')
    assert fssl._challenge_plan('pma.example.com') == ('http-01',['--webroot','-w','/srv/phpmyadmin'])
    assert fssl.DEPLOY_HOOK_SCRIPT == '/opt/boron/scripts/ssl_deploy_hook.py'


def test_phpmyadmin_certificate_status_is_treated_as_system_hostname(tmp_path, monkeypatch):
    monkeypatch.setattr(fssl.settings, 'pma_hostname', 'pma.example.com')
    monkeypatch.setattr(fssl, 'letsencrypt_cert_paths', lambda domain: (str(tmp_path / 'key'), str(tmp_path / 'cert')))
    assert fssl.certificate_status({'domain': 'pma.example.com'})['ssl_status'] == 'none'
