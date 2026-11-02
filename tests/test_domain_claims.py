"""Which hostnames an account may claim.

Regressions for two findings: a customer could add a public mail provider
(gmail.com) or any other external domain to capture mail, and the ownership
check ignored descendant names, so a customer could pre-claim
www./mail. of another tenant's future domain and take it over later.
"""
import pytest
from sqlalchemy import select

from daemon import domain_ownership, handlers_account, handlers_dns, handlers_domain, identity_admin, parked
from shared.config import settings
from shared.db import write_session
from shared.models import Account, DnsServerSettings, DnsZone, Domain, MailDomain
from shared.validation import ValidationError


@pytest.fixture
def tenants(isolated_db, monkeypatch):
    monkeypatch.setattr(handlers_domain, "ensure_docroot", lambda *a, **kw: None)
    monkeypatch.setattr(handlers_domain.ols, "provision_vhost", lambda *a: None)
    monkeypatch.setattr(handlers_domain, "_create_managed_zone", lambda username, domain: {"zone": domain})
    monkeypatch.setattr(handlers_account.sysops, "create_linux_user",
                        lambda *a: pytest.fail("account creation must stop before Linux side effects"))
    with write_session() as db:
        alice, bob = Account(username="alice", status="active"), Account(username="bob", status="active")
        db.add_all([alice, bob])
        db.flush()
        db.add(Domain(account_id=alice.id, domain="alice.example", docroot="/home/alice/public_html", kind="primary"))
        db.add(Domain(account_id=bob.id, domain="bob.example", docroot="/home/bob/public_html", kind="primary"))
        return alice.id, bob.id


def _claim(route, username, domain):
    if route == "addon":
        return handlers_domain.add_domain({"username": username, "domain": domain})
    if route == "parked":
        return parked.add_parked_domain({"username": username, "parked_domain": domain})
    if route == "primary":
        return identity_admin.set_primary_domain({"username": username, "domain": domain})
    return handlers_account.create_account({"username": "charlie", "primary_domain": domain})


ROUTES = ["addon", "parked", "primary", "account"]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("domain", ["gmail.com", "mx.gmail.com", "outlook.com", "co.uk", "github.io"])
def test_public_providers_and_suffixes_cannot_be_claimed(tenants, route, domain):
    with pytest.raises(ValidationError):
        _claim(route, "alice", domain)
    with write_session() as db:
        assert db.scalar(select(Domain).where(Domain.domain == domain)) is None


@pytest.mark.parametrize("route", ROUTES)
def test_operator_reserved_and_infrastructure_names_cannot_be_claimed(tenants, route, monkeypatch):
    monkeypatch.setattr(settings, "reserved_domains", ("corp-mail.example",))
    monkeypatch.setattr(settings, "mail_hostname", "mx.host.example")
    with write_session() as db:
        db.add(DnsServerSettings(id=1, local_nameservers=["ns1.host.example", "ns2.host.example"]))
    for name in ("corp-mail.example", "smtp.corp-mail.example", "mx.host.example", "ns1.host.example",
                 "www.ns2.host.example"):
        with pytest.raises(ValidationError):
            _claim(route, "alice", name)


def test_parent_of_infrastructure_name_stays_claimable(tenants, monkeypatch):
    monkeypatch.setattr(settings, "mail_hostname", "mx.alice.example")
    assert domain_ownership.reserved_reason("alice.example") is None


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("owned_by_bob", ["www.victim.example", "deep.mail.victim.example"])
def test_parent_cannot_be_added_over_another_accounts_subdomain(tenants, route, owned_by_bob):
    _, bob = tenants
    with write_session() as db:
        db.add(Domain(account_id=bob, domain=owned_by_bob, docroot="/home/bob/x", kind="addon"))
    with pytest.raises(ValidationError, match="subdomain of this domain"):
        _claim(route, "alice", "victim.example")
    with write_session() as db:
        assert db.scalar(select(Domain).where(Domain.domain == "victim.example")) is None


@pytest.mark.parametrize("resource", ["zone", "mail"])
def test_parent_blocked_by_foreign_descendant_zone_or_mail(tenants, resource):
    _, bob = tenants
    with write_session() as db:
        if resource == "zone":
            db.add(DnsZone(zone="www.victim.example", account_id=bob))
        else:
            db.add(MailDomain(domain="mail.victim.example", account_id=bob))
    with pytest.raises(ValidationError, match="subdomain of this domain"):
        handlers_domain.add_domain({"username": "alice", "domain": "victim.example"})


@pytest.mark.parametrize("route", ROUTES)
def test_subdomain_of_another_accounts_domain_is_refused_without_a_zone(tenants, route):
    # No DnsZone exists for bob.example (e.g. its DNS is hosted elsewhere),
    # which the previous zone-only check needed to notice the conflict.
    with pytest.raises(ValidationError, match="belongs to another account"):
        _claim(route, "alice", "www.bob.example")


def test_squatter_cannot_preclaim_www_of_future_domain_then_owner_add_fails_cleanly(tenants):
    # Mallory (bob) claims www.victim.example before the real owner arrives.
    handlers_domain.add_domain({"username": "bob", "domain": "www.victim.example"})
    with pytest.raises(ValidationError, match="Ask the server administrator"):
        handlers_domain.add_domain({"username": "alice", "domain": "victim.example"})


def test_same_account_may_nest_names(tenants):
    handlers_domain.add_domain({"username": "alice", "domain": "shop.alice.example"})
    handlers_domain.add_domain({"username": "bob", "domain": "other.example"})
    handlers_domain.add_domain({"username": "bob", "domain": "www.other.example"})
    with write_session() as db:
        names = set(db.scalars(select(Domain.domain)).all())
    assert {"shop.alice.example", "other.example", "www.other.example"} <= names


def test_sibling_names_of_different_accounts_are_independent(tenants):
    handlers_domain.add_domain({"username": "bob", "domain": "blog.shared.example"})
    handlers_domain.add_domain({"username": "alice", "domain": "shop.shared.example"})


def test_ancestor_check_ignores_public_suffix_rows():
    # A legacy row for a public suffix must not lock out every name below it.
    assert "co.uk" not in [name for name in domain_ownership._ancestors("shop.example.co.uk")
                           if not domain_ownership.is_public_suffix(name)]


def test_zone_creation_refuses_to_span_foreign_names(tenants, monkeypatch):
    _, bob = tenants
    with write_session() as db:
        db.add(Domain(account_id=bob, domain="www.victim.example", docroot="/home/bob/x", kind="addon"))
    monkeypatch.setattr(handlers_dns.dnsprovider, "zone_exists",
                        lambda zone: pytest.fail("ownership must be checked before PowerDNS"))
    with pytest.raises(ValidationError, match="subdomain of this domain"):
        handlers_dns.create_zone({"username": "alice", "domain": "victim.example"})
    with pytest.raises(ValidationError, match="belongs to another account"):
        handlers_dns.create_zone({"username": "alice", "domain": "deep.bob.example"})


def test_mail_only_domain_cannot_span_another_accounts_names(tenants, monkeypatch):
    # handlers_mail.create_mail_domain with an explicit username and no
    # hosting Domain row (admin call, archive restore) skips
    # require_available, so it must apply the overlap rule itself.
    from daemon import handlers_mail

    monkeypatch.setattr(handlers_mail.mail, "domain_exists", lambda domain: False)
    monkeypatch.setattr(handlers_mail.mail, "create_mail_domain",
                        lambda *a, **kw: pytest.fail("ownership must be checked before MariaDB"))
    with pytest.raises(ValidationError, match="belongs to another account"):
        handlers_mail.create_mail_domain({"username": "alice", "domain": "www.bob.example"})
    _, bob = tenants
    with write_session() as db:
        db.add(Domain(account_id=bob, domain="www.victim.example", docroot="/home/bob/x", kind="addon"))
    with pytest.raises(ValidationError, match="subdomain of this domain"):
        handlers_mail.create_mail_domain({"username": "alice", "domain": "victim.example"})
