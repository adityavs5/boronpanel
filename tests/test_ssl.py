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
