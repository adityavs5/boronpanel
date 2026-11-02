import datetime as dt

import pytest

from daemon import handlers_account as ha
from daemon import handlers_domain as hd
from daemon import ssl as fssl

_local_authority = fssl._local_dns_is_publicly_authoritative
from shared.db import write_session
from shared.models import DnsZone


@pytest.fixture()
def stub_sysops(monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)


@pytest.fixture()
def stub_filesystem(monkeypatch):
    monkeypatch.setattr(hd, "ensure_docroot", lambda username, docroot, domain_name=None, **kwargs: None)
    monkeypatch.setattr(hd.ols, "provision_vhost", lambda account: None)


@pytest.fixture(autouse=True)
def stub_zone_creation(monkeypatch):
    """Keep SSL unit tests independent of the live PowerDNS API.

    Individual tests add a DnsZone row when they need to exercise a managed
    DNS challenge.  Domain creation itself is not the behavior under test in
    this module.
    """
    monkeypatch.setattr(fssl, "_local_dns_is_publicly_authoritative", lambda zone: True)
    monkeypatch.setattr(
        hd,
        "_create_managed_zone",
        lambda username, domain: {"zone": domain},
    )
    # No public DNS from unit tests: www aliases do not resolve unless a
    # test says otherwise.
    monkeypatch.setattr(fssl, "_public_addresses", lambda hostname: set())
    monkeypatch.setattr(fssl, "_server_addresses", lambda: {SERVER_IP})


SERVER_IP = "192.0.2.10"


def _capture_certbot(monkeypatch):
    from daemon.procutil import ProcResult

    monkeypatch.setattr(fssl.settings, "letsencrypt_email", "ops@example.com")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(fssl, "run", fake_run)
    return calls


def _requested_names(args):
    return [args[index + 1] for index, value in enumerate(args) if value == "-d"]


def test_www_is_on_the_certificate_when_it_resolves_here(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    """Live finding UW H1: certbot ran with -d <apex> only, so
    https://www.<domain> failed with a name mismatch although the vhost
    serves www and www resolved to the server."""
    calls = _capture_certbot(monkeypatch)
    lookups = []
    monkeypatch.setattr(fssl, "_public_addresses", lambda hostname: lookups.append(hostname) or {SERVER_IP})
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    result = fssl.issue_certificate({"domain": "demo1.example"})

    assert _requested_names(calls[0]) == ["demo1.example", "www.demo1.example"]
    assert "--expand" in calls[0] and calls[0][calls[0].index("--cert-name") + 1] == "demo1.example"
    assert result["names"] == ["demo1.example", "www.demo1.example"]
    assert "skipped_names" not in result
    assert lookups == ["www.demo1.example"]


@pytest.mark.parametrize("answer,reason", [
    ({"198.51.100.7"}, "does not point to this server"),
    ({SERVER_IP, "198.51.100.7"}, "does not point to this server"),
    (set(), "does not point to this server"),
    (None, "could not be checked in public DNS"),
])
def test_www_is_left_out_when_it_does_not_resolve_here(isolated_db, stub_sysops, stub_filesystem, monkeypatch,
                                                       answer, reason):
    calls = _capture_certbot(monkeypatch)
    monkeypatch.setattr(fssl, "_public_addresses", lambda hostname: answer)
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    result = fssl.issue_certificate({"domain": "demo1.example"})

    assert _requested_names(calls[0]) == ["demo1.example"]
    assert "--expand" not in calls[0]
    assert result["skipped_names"] == [{"name": "www.demo1.example", "reason": reason}]


def test_reissue_keeps_www_when_public_dns_cannot_be_asked(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    """Renewals keep their names: a resolver outage during a panel renew
    must not drop www from a certificate that already covers it."""
    calls = _capture_certbot(monkeypatch)
    monkeypatch.setattr(fssl, "_public_addresses", lambda hostname: None)
    monkeypatch.setattr(fssl, "_certificate_san", lambda domain: {"demo1.example", "www.demo1.example"})
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    fssl.issue_certificate({"domain": "demo1.example", "force": True})

    assert _requested_names(calls[0]) == ["demo1.example", "www.demo1.example"]


def test_www_of_a_separate_site_is_never_requested(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    calls = _capture_certbot(monkeypatch)
    monkeypatch.setattr(fssl, "_public_addresses", lambda hostname: pytest.fail("no lookup for a separate site"))
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    hd.add_domain({"username": "demo1", "domain": "www.demo1.example", "kind": "addon"})

    fssl.issue_certificate({"domain": "demo1.example"})
    fssl.issue_certificate({"domain": "www.demo1.example"})

    assert [_requested_names(args) for args in calls] == [["demo1.example"], ["www.demo1.example"]]


def test_service_hostname_certificate_has_one_name(isolated_db, monkeypatch):
    calls = _capture_certbot(monkeypatch)
    monkeypatch.setattr(fssl.settings, "webmail_hostname", "webmail.example.com")
    monkeypatch.setattr(fssl.settings, "mail_hostname", "webmail.example.com")
    monkeypatch.setattr(fssl, "service_certificate_diagnostics", lambda params: {
        "dns": {"ok": True}, "http_challenge": {"ok": True}, "sni": {"ok": True}})
    from scripts import ssl_deploy_hook
    monkeypatch.setattr(ssl_deploy_hook, "_deploy_mail_service_certificate", lambda domain: None)

    fssl.issue_certificate({"domain": "webmail.example.com"})

    assert _requested_names(calls[0]) == ["webmail.example.com"]


def test_cloudflare_proxied_www_counts_as_pointing_here(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    """A proxied name resolves to Cloudflare's edge; the zone's own record
    content says whether it reaches this server."""
    from shared.models import CloudflareZone

    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    with write_session() as session:
        session.add(DnsZone(account_id=1, zone="demo1.example"))
        session.add(CloudflareZone(account_id=1, zone="demo1.example", cf_zone_id="z1", status="active",
                                   name_servers=[], cf_account_id=None))
    records = [
        {"name": "demo1.example", "type": "A", "values": [SERVER_IP], "proxied": True},
        {"name": "www.demo1.example", "type": "CNAME", "values": ["demo1.example."], "proxied": True},
        {"name": "shop.demo1.example", "type": "A", "values": ["198.51.100.7"], "proxied": True},
    ]
    monkeypatch.setattr(fssl.dnsprovider, "list_records", lambda zone: records)
    monkeypatch.setattr(fssl, "_public_addresses", lambda hostname: {"104.16.0.1"})

    assert fssl._points_here("www.demo1.example", {SERVER_IP}) is True
    assert fssl._points_here("shop.demo1.example", {SERVER_IP}) is False
    assert fssl._points_here("www.other.example", {SERVER_IP}) is False


def test_prepare_https_uses_a_covering_certificate_or_issues_one(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    """Live finding UW L4: a clone or staging site configured with an https
    address got no certificate, so its sign-in failed until SSL was issued."""
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    issued = []
    covered = {"names": set()}
    monkeypatch.setattr(fssl, "_cert_file_details", lambda domain: {"cert_status": "valid"} if covered["names"] else None)
    monkeypatch.setattr(fssl, "_certificate_san", lambda domain: covered["names"])

    def issue(params):
        issued.append(params["domain"])
        covered["names"] = {"demo1.example", "www.demo1.example"}
        _activate_domain_ssl("demo1.example")
        return {"status": "issued"}

    monkeypatch.setattr(fssl, "issue_certificate", issue)

    monkeypatch.setattr(fssl, "_public_addresses", lambda hostname: {"198.51.100.7"})
    assert fssl.prepare_https("demo1.example", "www.demo1.example") == {
        "https": False, "certificate": None, "detail": "www.demo1.example does not point to this server yet"}
    monkeypatch.setattr(fssl, "_public_addresses", lambda hostname: None)
    assert fssl.prepare_https("demo1.example")["detail"] == "public DNS for demo1.example could not be checked"
    assert issued == []

    monkeypatch.setattr(fssl, "_public_addresses", lambda hostname: {SERVER_IP})
    assert fssl.prepare_https("demo1.example", "www.demo1.example") == {
        "https": True, "certificate": "issued", "detail": None}
    assert fssl.prepare_https("demo1.example", "www.demo1.example")["certificate"] == "existing"
    assert issued == ["demo1.example"]


def test_prepare_https_reports_a_failed_issuance(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    monkeypatch.setattr(fssl, "_public_addresses", lambda hostname: {SERVER_IP})
    monkeypatch.setattr(fssl, "issue_certificate", lambda params: (_ for _ in ()).throw(fssl.SslError("certbot failed")))
    assert fssl.prepare_https("demo1.example") == {
        "https": False, "certificate": None, "detail": "a certificate for demo1.example could not be issued"}


def test_delete_certificate_removes_a_leftover_lineage(isolated_db, monkeypatch, tmp_path):
    from shared.models import SslExpiryNotice

    calls = []
    monkeypatch.setattr(fssl, "run", lambda args, **kwargs: calls.append(args) or type("R", (), {"ok": True})())
    monkeypatch.setattr(fssl, "RENEWAL_CONF_DIR", tmp_path)
    with write_session() as session:
        session.add(SslExpiryNotice(domain="gone.example", expiry_date="2026-12-01"))
    fssl.delete_certificate("gone.example")
    assert calls == []  # no lineage: nothing to delete
    with write_session() as session:
        assert session.scalars(fssl.select(SslExpiryNotice)).all() == []

    (tmp_path / "gone.example.conf").write_text("# renewal")
    fssl.delete_certificate("gone.example")
    assert calls == [[fssl.settings.certbot_bin, "delete", "--cert-name", "gone.example", "--non-interactive"]]

    monkeypatch.setattr(fssl.settings, "webmail_hostname", "webmail.example.com")
    (tmp_path / "webmail.example.com.conf").write_text("# renewal")
    fssl.delete_certificate("webmail.example.com")
    assert len(calls) == 1  # a service hostname's lineage is never a site's


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

    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    # Only now: domain add also deletes a leftover lineage of the name.
    monkeypatch.setattr(fssl.Path, "exists", lambda self: True)

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
    from shared.db import write_session
    from shared.models import Account
    with write_session() as session:
        session.query(Account).filter_by(username="bravo").one().status = "terminated"
    result = fssl.get_admin_ssl_dashboard({})
    assert result["certbot_timer_active"] is True
    assert {(row["username"], row["domain"]) for row in result["domains"]} == {
        ("alpha", "alpha.example"),
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


def test_ordinary_certificate_uses_http_for_undelegated_local_zone(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    with write_session() as session:
        session.add(DnsZone(account_id=1, zone="demo1.example"))
    monkeypatch.setattr(fssl, "_local_dns_is_publicly_authoritative", lambda zone: False)
    mode, args = fssl._challenge_plan("demo1.example")
    assert mode == "http-01"
    assert "dns-powerdns" not in args


@pytest.mark.parametrize("actual, expected_result", [(["ns1.example.com"], True), (["ns1.example.com", "other.example.com"], False), ([], False)])
def test_local_challenge_checks_public_delegation(monkeypatch, actual, expected_result):
    import dns.resolver
    from types import SimpleNamespace
    monkeypatch.setattr(fssl.dnsprovider, "list_records", lambda zone: [{"name": zone, "type": "NS", "values": ["ns1.example.com.", "ns2.example.com."]}])
    class Resolver:
        def __init__(self, *, configure):
            assert configure is False
        def resolve(self, zone, kind, *, lifetime):
            assert self.nameservers == ["1.1.1.1", "8.8.8.8"] and kind == "NS"
            return [SimpleNamespace(target=name) for name in actual]
    monkeypatch.setattr(dns.resolver, "Resolver", Resolver)
    assert _local_authority("example.com") is expected_result


def test_retained_canonical_mail_certificate_still_repairs_service_deployment(isolated_db, monkeypatch):
    from scripts import ssl_deploy_hook
    from daemon.procutil import ProcResult
    monkeypatch.setattr(fssl.settings, 'letsencrypt_email', 'ops@example.com')
    monkeypatch.setattr(fssl.settings, 'webmail_hostname', 'webmail.example.com')
    monkeypatch.setattr(fssl.settings, 'mail_hostname', '')
    monkeypatch.setattr(fssl, 'service_certificate_diagnostics', lambda params: {
        'dns': {'ok': True}, 'http_challenge': {'ok': True}, 'sni': {'ok': True}})
    monkeypatch.setattr(fssl, 'run', lambda args, **kw: ProcResult(args, 0, 'Certificate not yet due for renewal', ''))
    applied = []
    monkeypatch.setattr(ssl_deploy_hook, '_deploy_mail_service_certificate', applied.append)
    assert fssl.issue_certificate({'domain': 'webmail.example.com'})['status'] == 'issued'
    assert applied == ['webmail.example.com']
