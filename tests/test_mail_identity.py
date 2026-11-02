"""The server's mail identity: Postfix hostname and the mail TLS readiness row."""
import datetime as dt
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from daemon import diagnostics, mail_identity
from shared.db import write_session
from shared.models import Account, MailDomain


@pytest.fixture()
def hostnames(monkeypatch):
    def configure(mail="", webmail="", panel=""):
        monkeypatch.setattr(mail_identity.settings, "mail_hostname", mail)
        monkeypatch.setattr(mail_identity.settings, "webmail_hostname", webmail)
        monkeypatch.setattr(mail_identity.settings, "panel_hostname", panel)
    return configure


class FakePostfix:
    def __init__(self, **values):
        self.values = {"myhostname": "henchkin-node-a", "smtp_helo_name": "$myhostname", **values}
        self.commands = []

    def __call__(self, args, **kwargs):
        self.commands.append(args)
        if args[:2] == ["postconf", "-h"]:
            return SimpleNamespace(ok=True, returncode=0, stdout=self.values[args[2]] + "\n", stderr="")
        if args[:2] == ["postconf", "-e"]:
            name, _, value = args[2].partition("=")
            self.values[name] = value
        return SimpleNamespace(ok=True, returncode=0, stdout="", stderr="")


def test_canonical_hostname_prefers_mail_then_webmail_then_panel(hostnames):
    hostnames(panel="panel.example.test")
    assert mail_identity.canonical_hostname() == "panel.example.test"
    hostnames(webmail="webmail.example.test", panel="panel.example.test")
    assert mail_identity.canonical_hostname() == "webmail.example.test"
    hostnames(mail="Mail.Example.Test", webmail="webmail.example.test")
    assert mail_identity.canonical_hostname() == "mail.example.test"
    hostnames()
    assert mail_identity.canonical_hostname() is None


def test_postfix_greets_and_bounces_as_the_mail_hostname(isolated_db, hostnames):
    """Live finding: myhostname was the VM's bare name, so HELO and
    MAILER-DAEMON bounces used 'henchkin-node-a'."""
    hostnames(webmail="webmail.example.test", panel="panel.example.test")
    postfix = FakePostfix(smtp_helo_name="other.example.test")
    result = mail_identity.configure_postfix_identity(postfix)
    assert result == {"myhostname": "webmail.example.test", "changed": True}
    assert ["postconf", "-e", "myhostname=webmail.example.test"] in postfix.commands
    assert ["postconf", "-e", "smtp_helo_name=$myhostname"] in postfix.commands
    postfix.commands.clear()
    assert mail_identity.configure_postfix_identity(postfix)["changed"] is False
    assert not any(args[:2] == ["postconf", "-e"] for args in postfix.commands)


def test_mail_hostname_that_is_a_hosted_mail_domain_is_not_taken_over(isolated_db, hostnames):
    hostnames(panel="panel.example.test")
    with write_session() as db:
        account = Account(username="mailone", status="active")
        db.add(account)
        db.flush()
        db.add(MailDomain(account_id=account.id, domain="panel.example.test"))
    postfix = FakePostfix()
    result = mail_identity.configure_postfix_identity(postfix)
    assert result["changed"] is False and "hosted mail domain" in result["detail"]
    assert postfix.commands == []


def test_no_mail_hostname_leaves_postfix_alone(isolated_db, hostnames):
    hostnames()
    postfix = FakePostfix()
    assert mail_identity.configure_postfix_identity(postfix)["myhostname"] is None
    assert postfix.commands == []


def test_server_setup_reconciles_postfix_only_for_identity_settings(isolated_db, hostnames, monkeypatch):
    hostnames(webmail="webmail.example.test")
    postfix = FakePostfix()
    monkeypatch.setattr(mail_identity, "_run", postfix)
    monkeypatch.setattr(mail_identity.shutil, "which", lambda name: "/usr/sbin/" + name)
    assert mail_identity.apply_postfix_identity(changed={"update_channel": "beta"})["changed"] is False
    assert postfix.commands == []
    assert mail_identity.apply_postfix_identity(changed={"webmail_hostname": "webmail.example.test"})["changed"]
    assert ["postfix", "check"] in postfix.commands
    assert ["systemctl", "reload", "postfix"] in postfix.commands

    def broken(args, **kwargs):
        raise RuntimeError("postconf: fatal")

    monkeypatch.setattr(mail_identity, "_run", broken)
    # A Postfix problem never undoes the hostname change itself.
    assert mail_identity.apply_postfix_identity(changed={"panel_hostname": "x"})["changed"] is False


def test_server_setup_save_calls_the_postfix_reconcile():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "daemon" / "panel_config.py").read_text()
    assert "mail_identity.apply_postfix_identity(changed=clean)" in source


def _certificate(tmp_path, name, hostname, days=60):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=days))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
            .sign(key, hashes.SHA256()))
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return path


@pytest.fixture()
def readiness(isolated_db, tmp_path, hostnames, monkeypatch):
    hostnames(webmail="webmail.example.test", panel="panel.example.test")
    monkeypatch.setattr(mail_identity.settings, "server_public_ip", "192.0.2.10")
    monkeypatch.setattr(mail_identity, "LETSENCRYPT_LIVE_DIR", tmp_path / "live")
    monkeypatch.setattr(mail_identity, "MAIL_CERT_PATH", tmp_path / "ssl" / "mail.crt")
    monkeypatch.setattr(mail_identity.shutil, "which", lambda name: "/usr/sbin/" + name)
    monkeypatch.setattr(mail_identity, "_server_addresses", lambda: {"192.0.2.10"})
    monkeypatch.setattr(mail_identity, "_resolved", lambda hostname: ["192.0.2.10"])
    postfix = FakePostfix(myhostname="webmail.example.test")
    monkeypatch.setattr(mail_identity, "_run", postfix)
    return SimpleNamespace(tmp=tmp_path, postfix=postfix)


def test_readiness_names_the_missing_dns_record(readiness, monkeypatch):
    monkeypatch.setattr(mail_identity, "_resolved", lambda hostname: [])
    status, explanation, fix = mail_identity._readiness()
    assert status == "fail" and "does not resolve" in explanation
    assert "A record for webmail.example.test pointing to 192.0.2.10" in fix
    assert "Server Setup step 6" in fix
    monkeypatch.setattr(mail_identity, "_resolved", lambda hostname: ["198.51.100.7"])
    status, explanation, fix = mail_identity._readiness()
    assert status == "fail" and "198.51.100.7, not to this server" in explanation


def test_readiness_names_the_certificate_step_and_the_deployment_gap(readiness):
    _certificate(readiness.tmp, "ssl/mail.crt", "boron-default")
    status, explanation, fix = mail_identity._readiness()
    assert status == "fail" and "No Let's Encrypt certificate exists for webmail.example.test" in explanation
    assert "CN=boron-default" in explanation
    assert "Issue the Webmail certificate" in fix and "Server Setup step 6" in fix

    _certificate(readiness.tmp, "live/webmail.example.test/fullchain.pem", "webmail.example.test")
    status, explanation, fix = mail_identity._readiness()
    assert status == "fail" and "still present CN=boron-default" in explanation
    assert "Renew webmail.example.test" in fix


def test_readiness_passes_once_postfix_and_dovecot_use_the_certificate(readiness):
    lineage = _certificate(readiness.tmp, "live/webmail.example.test/fullchain.pem", "webmail.example.test")
    (readiness.tmp / "ssl").mkdir()
    (readiness.tmp / "ssl" / "mail.crt").write_bytes(lineage.read_bytes())
    status, explanation, _fix = mail_identity._readiness()
    assert status == "pass" and "Postfix uses it as its hostname" in explanation

    readiness.postfix.values["myhostname"] = "henchkin-node-a"
    status, explanation, fix = mail_identity._readiness()
    assert status == "warn" and "identifies itself as henchkin-node-a" in explanation
    assert "Server Setup step 4" in fix


def test_readiness_for_a_separate_mail_hostname_explains_its_validation(readiness, hostnames):
    hostnames(mail="smtp.example.test", webmail="webmail.example.test", panel="panel.example.test")
    status, _explanation, fix = mail_identity._readiness()
    assert status == "fail" and "Let's Encrypt cannot validate smtp.example.test" in fix
    assert "the mail hostname then follows the webmail hostname" in fix


def test_readiness_without_a_mail_hostname(readiness, hostnames):
    hostnames()
    status, explanation, fix = mail_identity._readiness()
    assert status == "warn" and "No mail hostname is configured" in explanation
    assert "Server Setup step 4" in fix


def test_mail_identity_is_a_diagnostics_row(readiness):
    assert ("mail_identity", mail_identity.readiness_check) in diagnostics.check_registry()
    row = mail_identity.readiness_check()
    assert row["id"] == "mail_identity" and row["status"] in diagnostics.STATUS_VALUES
