"""Local mail delivery waits for the domain's public MX to point here.

Regression for the finding that any customer could add an external domain
(gmail.com, a victim company's domain) and, by provisioning mail for it, make
Postfix deliver every message other tenants and the panel sent to that domain
into the customer's own mailboxes.
"""
import types

import dns.exception
import dns.resolver
import pytest
from sqlalchemy import select

from daemon import handlers_email_routing as er
from daemon import handlers_mail as hm
from daemon import mail_local_delivery as mld
from shared.config import settings
from shared.db import write_session
from shared.models import Account, Domain, EmailRouting, MailDomain
from shared.validation import ValidationError

HERE_IP = "203.0.113.10"
OTHER_IP = "198.51.100.20"


class FakeResolver:
    def __init__(self, records):
        self.records = records
        self.queries = []

    def resolve(self, name, rtype):
        self.queries.append((name, rtype))
        value = self.records.get((name, rtype), dns.resolver.NoAnswer)
        if isinstance(value, type) and issubclass(value, Exception):
            raise value()
        if isinstance(value, Exception):
            raise value
        if rtype == "MX":
            return [types.SimpleNamespace(preference=pref, exchange=host + ".") for pref, host in value]
        return [types.SimpleNamespace(to_text=lambda ip=ip: ip) for ip in value]


@pytest.fixture
def dns_records(monkeypatch):
    records = {}
    resolver = FakeResolver(records)
    monkeypatch.setattr(mld, "_resolver", lambda: resolver)
    monkeypatch.setattr(mld, "server_addresses", lambda: {HERE_IP})
    return records


def test_mx_pointing_here_is_local(dns_records):
    dns_records[("hosted.example", "MX")] = [(10, "mx.hosted.example")]
    dns_records[("mx.hosted.example", "A")] = [HERE_IP]
    assert mld.public_mx_status("hosted.example") == mld.HERE


def test_external_provider_mx_is_elsewhere(dns_records):
    dns_records[("victim.example", "MX")] = [(5, "aspmx.l.google.com")]
    dns_records[("aspmx.l.google.com", "A")] = [OTHER_IP]
    assert mld.public_mx_status("victim.example") == mld.ELSEWHERE


def test_only_most_preferred_mx_counts(dns_records):
    # A backup MX on this server does not make the domain local.
    dns_records[("split.example", "MX")] = [(10, "primary.elsewhere.example"), (20, "backup.here.example")]
    dns_records[("primary.elsewhere.example", "A")] = [OTHER_IP]
    dns_records[("backup.here.example", "A")] = [HERE_IP]
    assert mld.public_mx_status("split.example") == mld.ELSEWHERE


def test_implicit_mx_and_null_mx_and_nxdomain(dns_records):
    dns_records[("implicit.example", "MX")] = dns.resolver.NoAnswer
    dns_records[("implicit.example", "A")] = [HERE_IP]
    assert mld.public_mx_status("implicit.example") == mld.HERE
    dns_records[("nullmx.example", "MX")] = [(0, "")]
    assert mld.public_mx_status("nullmx.example") == mld.ELSEWHERE
    dns_records[("gone.example", "MX")] = dns.resolver.NXDOMAIN
    assert mld.public_mx_status("gone.example") == mld.ELSEWHERE


def test_lookup_errors_are_unknown_not_local(dns_records):
    dns_records[("flaky.example", "MX")] = dns.exception.Timeout
    assert mld.public_mx_status("flaky.example") == mld.UNKNOWN
    dns_records[("half.example", "MX")] = [(10, "mx.half.example")]
    dns_records[("mx.half.example", "A")] = dns.exception.Timeout
    assert mld.public_mx_status("half.example") == mld.UNKNOWN


def test_server_addresses_ignore_private_and_loopback(monkeypatch, isolated_db):
    from daemon import ipmanager
    monkeypatch.setattr(ipmanager, "detected_addresses", lambda: [
        {"address": "127.0.0.1"}, {"address": "10.0.0.5"}, {"address": "192.0.2.7"}, {"address": "1.2.3.4"}])
    monkeypatch.setattr(settings, "server_public_ip", "")
    assert mld.server_addresses() == {"1.2.3.4"}


@pytest.fixture
def mail_world(isolated_db, monkeypatch, tmp_path):
    """A customer who added gmail-like external and real hosted domains."""
    state = {"active": {}}
    monkeypatch.setattr(settings, "dkim_base_dir", str(tmp_path / "dkim"))
    monkeypatch.setattr(hm.dkim, "setup_dns_signing", lambda domain: {})
    monkeypatch.setattr(hm.mail, "domain_exists", lambda d: d in state["active"])

    def create_mail_domain(domain, active=True):
        state["active"][domain] = active
        return 1

    def set_domain_active(domain, active):
        if domain not in state["active"]:
            return False
        state["active"][domain] = active
        return True

    monkeypatch.setattr(hm.mail, "create_mail_domain", create_mail_domain)
    monkeypatch.setattr(hm.mail, "set_domain_active", set_domain_active)
    monkeypatch.setattr(hm.mail, "domain_active", lambda d: state["active"].get(d))
    monkeypatch.setattr(er, "run", lambda *a, **k: None)
    monkeypatch.setattr(settings, "postfix_relay_domains_map", str(tmp_path / "relay"))
    with write_session() as db:
        account = Account(username="mallory", status="active", uid=5001, gid=5001)
        db.add(account)
        db.flush()
        for name in ("victimcorp.example", "mallory.example"):
            db.add(Domain(account_id=account.id, domain=name, kind="addon", docroot=f"/home/mallory/{name}"))
    return state


def test_claimed_external_domain_is_not_accepted_locally(mail_world, dns_records):
    dns_records[("victimcorp.example", "MX")] = [(10, "mx.victimcorp.example")]
    dns_records[("mx.victimcorp.example", "A")] = [OTHER_IP]
    result = hm.create_mail_domain({"username": "mallory", "domain": "victimcorp.example"})
    assert result["local_delivery"] == "pending_mx"
    assert mail_world["active"]["victimcorp.example"] is False
    # Explicitly asking for local routing does not bypass the check either.
    routed = er.set_routing({"username": "mallory", "domain": "victimcorp.example", "mode": "local"})
    assert routed["accepting_locally"] is False and routed["pending_mx"] is True
    assert mail_world["active"]["victimcorp.example"] is False


def test_hosted_domain_is_accepted_and_pending_domain_activates_after_mx_change(mail_world, dns_records):
    dns_records[("mallory.example", "MX")] = [(10, "mail.mallory.example")]
    dns_records[("mail.mallory.example", "A")] = [OTHER_IP]
    assert hm.create_mail_domain({"username": "mallory", "domain": "mallory.example"})["local_delivery"] == "pending_mx"
    report = mld.reconcile()
    assert report["pending"] == ["mallory.example"] and mail_world["active"]["mallory.example"] is False
    dns_records[("mail.mallory.example", "A")] = [HERE_IP]
    report = mld.reconcile()
    assert report["activated"] == ["mallory.example"]
    assert mail_world["active"]["mallory.example"] is True


def test_remote_mode_domains_are_left_alone_by_reconcile(mail_world, dns_records):
    dns_records[("mallory.example", "MX")] = [(10, "mail.mallory.example")]
    dns_records[("mail.mallory.example", "A")] = [HERE_IP]
    with write_session() as db:
        db.add(EmailRouting(domain="mallory.example", mode="remote"))
    assert hm.create_mail_domain({"username": "mallory", "domain": "mallory.example"})["local_delivery"] == "off"
    assert mld.reconcile()["activated"] == []
    assert mail_world["active"]["mallory.example"] is False


def test_trusted_domain_and_disabled_check_skip_mx(mail_world, dns_records, monkeypatch):
    dns_records[("victimcorp.example", "MX")] = dns.exception.Timeout
    monkeypatch.setattr(settings, "mail_local_delivery_trusted_domains", ("victimcorp.example",))
    assert hm.create_mail_domain({"username": "mallory", "domain": "victimcorp.example"})["local_delivery"] == "active"
    monkeypatch.setattr(settings, "mail_local_delivery_trusted_domains", ())
    monkeypatch.setattr(settings, "mail_local_delivery_requires_mx", False)
    assert mld.local_delivery_allowed("mallory.example") is True


def test_recheck_active_only_acts_on_definitive_answers(mail_world, dns_records):
    mail_world["active"].update({"victimcorp.example": True, "mallory.example": True})
    with write_session() as db:
        account_id = db.scalar(select(Account.id))
        db.add_all([MailDomain(account_id=account_id, domain="victimcorp.example"),
                    MailDomain(account_id=account_id, domain="mallory.example")])
    dns_records[("victimcorp.example", "MX")] = [(10, "mx.victimcorp.example")]
    dns_records[("mx.victimcorp.example", "A")] = [OTHER_IP]
    dns_records[("mallory.example", "MX")] = dns.exception.Timeout
    assert mld.reconcile()["deactivated"] == []  # the routine sweep never deactivates on DNS
    report = mld.reconcile(recheck_active=True)
    assert report["deactivated"] == ["victimcorp.example"]
    assert mail_world["active"] == {"victimcorp.example": False, "mallory.example": True}


def test_public_provider_mail_domain_is_refused_and_swept(mail_world, dns_records):
    with pytest.raises(ValidationError, match="public mail"):
        hm.create_mail_domain({"username": "mallory", "domain": "gmail.com"})
    assert "gmail.com" not in mail_world["active"]
    # A row left over from before the deny-list existed is switched off.
    mail_world["active"]["gmail.com"] = True
    with write_session() as db:
        db.add(MailDomain(account_id=db.scalar(select(Account.id)), domain="gmail.com"))
    assert mld.reconcile()["deactivated"] == ["gmail.com"]
    assert mail_world["active"]["gmail.com"] is False


def test_reconcile_script_reports_errors(monkeypatch):
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "scripts" / "mail_local_delivery_reconcile.py"
    spec = importlib.util.spec_from_file_location("mail_local_delivery_reconcile", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    seen = {}
    monkeypatch.setattr(module.mail_local_delivery, "reconcile",
                        lambda recheck_active=False: seen.setdefault("report", {
                            "activated": [], "deactivated": [], "pending": [],
                            "errors": [{"domain": "x.example", "error": "boom"}] if recheck_active else []}))
    assert module.main([]) == 0
    seen.clear()
    assert module.main(["--recheck-active"]) == 1
