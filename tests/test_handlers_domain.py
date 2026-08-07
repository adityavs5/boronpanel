import pytest
from sqlalchemy import select

from daemon import handlers_account as ha
from daemon import handlers_domain as hd
from shared.db import write_session
from shared.models import Domain, DnsZone


@pytest.fixture()
def stub_sysops(monkeypatch):
    monkeypatch.setattr(ha.sysops, "create_linux_user", lambda username: (5001, 5001))
    monkeypatch.setattr(ha.sysops, "set_initial_password", lambda username, password: None)
    monkeypatch.setattr(ha.sysops, "set_quota", lambda username, soft, hard: None)


@pytest.fixture()
def stub_filesystem(monkeypatch):
    monkeypatch.setattr(hd, "ensure_docroot", lambda username, docroot, domain_name=None: None)


@pytest.fixture()
def stub_ols(monkeypatch):
    calls = {"provision": [], "remove": []}
    monkeypatch.setattr(hd.ols, "provision_vhost", lambda account: calls["provision"].append(account.username))
    monkeypatch.setattr(hd.ols, "remove_domain_vhost", lambda account, domain: calls["remove"].append((account.username, domain)))
    return calls


def _add_zone_owned_by(username: str, zone: str) -> None:
    # DnsZone.account_id is NOT NULL in the actual schema even though
    # handlers_dns.create_zone's own code accepts username=None and would
    # try to insert account_id=None -- a real, separately-scoped latent bug
    # this feature's tests happened to surface (see CHECKPOINT-phase2-4.md);
    # not fixed here since it's unrelated to subdomains and has zero live
    # rows affected. Every zone in these tests needs a real owning account.
    with write_session() as session:
        account = session.scalar(select(ha.Account).where(ha.Account.username == username))
        session.add(DnsZone(account_id=account.id, zone=zone))


@pytest.fixture()
def stub_powerdns(monkeypatch):
    calls = {"upsert": [], "delete": []}
    monkeypatch.setattr(hd.dnsprovider, "upsert_record", lambda zone, sub, rtype, values, ttl=None, proxied=False: calls["upsert"].append((zone, sub, rtype, tuple(values))))
    monkeypatch.setattr(hd.dnsprovider, "delete_record", lambda zone, sub, rtype: calls["delete"].append((zone, sub, rtype)))
    return calls


def test_add_domain_success_calls_provision_vhost(isolated_db, stub_sysops, stub_filesystem, stub_ols):
    ha.create_account({"username": "demo1"})
    result = hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    assert result["domain"] == "demo1.example"
    assert stub_ols["provision"] == ["demo1"]

    with write_session() as session:
        account = session.scalar(select(ha.Account).where(ha.Account.username == "demo1"))
        assert account.primary_domain == "demo1.example"


def test_add_domain_compensates_db_row_when_ols_apply_fails(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    def boom(account):
        raise RuntimeError("openlitespeed -t failed")

    monkeypatch.setattr(hd.ols, "provision_vhost", boom)

    ha.create_account({"username": "demo1"})
    with pytest.raises(RuntimeError):
        hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    with write_session() as session:
        orphan = session.scalar(select(Domain).where(Domain.domain == "demo1.example"))
        assert orphan is None, "a failed OLS apply must not leave an orphaned Domain row"

        account = session.scalar(select(ha.Account).where(ha.Account.username == "demo1"))
        assert account.primary_domain is None, "primary_domain must be cleared along with the orphaned row"


def test_add_domain_compensates_db_row_when_parent_lookup_fails(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    def boom(domain):
        raise RuntimeError("zone lookup failed")

    monkeypatch.setattr(hd, "_find_parent_zone", boom)

    ha.create_account({"username": "demo1"})
    with pytest.raises(RuntimeError, match="zone lookup failed"):
        hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    with write_session() as session:
        assert session.scalar(select(Domain).where(Domain.domain == "demo1.example")) is None
        account = session.scalar(select(ha.Account).where(ha.Account.username == "demo1"))
        assert account.primary_domain is None


def test_add_domain_rejects_duplicate_domain(isolated_db, stub_sysops, stub_filesystem, stub_ols):
    ha.create_account({"username": "demo1"})
    ha.create_account({"username": "demo2"})
    hd.add_domain({"username": "demo1", "domain": "shared.example", "kind": "primary"})
    with pytest.raises(RuntimeError):
        hd.add_domain({"username": "demo2", "domain": "shared.example", "kind": "primary"})


def test_add_domain_rejects_unknown_account(isolated_db, stub_sysops, stub_filesystem):
    with pytest.raises(RuntimeError):
        hd.add_domain({"username": "ghost", "domain": "ghost.example"})


def test_list_domains(isolated_db, stub_sysops, stub_filesystem, stub_ols):
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    hd.add_domain({"username": "demo1", "domain": "addon.example", "kind": "addon"})
    result = hd.list_domains({"username": "demo1"})
    domains = {d["domain"] for d in result["domains"]}
    assert domains == {"demo1.example", "addon.example"}


def test_add_subdomain_creates_dns_a_record_when_parent_zone_managed(isolated_db, stub_sysops, stub_filesystem, stub_ols, stub_powerdns, monkeypatch):
    monkeypatch.setattr(hd.settings, "server_public_ip", "203.0.113.10")
    ha.create_account({"username": "demo1"})
    _add_zone_owned_by("demo1", "demo1.example")
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    result = hd.add_domain({"username": "demo1", "domain": "blog.demo1.example", "kind": "subdomain"})

    assert result["dns_record_created"] is True
    # The primary domain add also matches its own zone (domain_name ==
    # parent_zone -> label "@"), so both get an A record automatically --
    # not just the subdomain under test here.
    assert ("demo1.example", "@", "A", ("203.0.113.10",)) in stub_powerdns["upsert"]
    assert ("demo1.example", "blog", "A", ("203.0.113.10",)) in stub_powerdns["upsert"]


def test_add_subdomain_skips_dns_when_zone_not_managed(isolated_db, stub_sysops, stub_filesystem, stub_ols, stub_powerdns, monkeypatch):
    monkeypatch.setattr(hd.settings, "server_public_ip", "203.0.113.10")
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    result = hd.add_domain({"username": "demo1", "domain": "blog.demo1.example", "kind": "subdomain"})

    assert result["dns_record_created"] is False
    assert stub_powerdns["upsert"] == []


def test_add_domain_dns_failure_is_compensated(isolated_db, stub_sysops, stub_filesystem, stub_ols, monkeypatch):
    monkeypatch.setattr(hd.settings, "server_public_ip", "203.0.113.10")
    deleted = []
    monkeypatch.setattr(hd.dnsprovider, "upsert_record", lambda zone, sub, rtype, values, ttl=None, proxied=False: None)
    monkeypatch.setattr(hd.dnsprovider, "delete_record", lambda zone, sub, rtype: deleted.append((zone, sub, rtype)))

    ha.create_account({"username": "demo1"})
    _add_zone_owned_by("demo1", "demo1.example")
    # provision_vhost must succeed for the primary domain (stub_ols) and
    # only start failing once the subdomain-under-test is added -- the
    # thing being tested is compensation of the DNS record THIS call
    # created, not a blanket OLS failure from the start.
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    def boom(account):
        raise RuntimeError("ols apply failed after dns record was created")

    monkeypatch.setattr(hd.ols, "provision_vhost", boom)
    with pytest.raises(RuntimeError):
        hd.add_domain({"username": "demo1", "domain": "blog.demo1.example", "kind": "subdomain"})

    assert deleted == [("demo1.example", "blog", "A")]
    with write_session() as session:
        orphan = session.scalar(select(Domain).where(Domain.domain == "blog.demo1.example"))
        assert orphan is None


def test_remove_domain_deletes_row_and_vhost(isolated_db, stub_sysops, stub_filesystem, stub_ols):
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    hd.add_domain({"username": "demo1", "domain": "addon.example", "kind": "addon"})

    result = hd.remove_domain({"username": "demo1", "domain": "addon.example"})
    assert result["status"] == "removed"
    assert ("demo1", "addon.example") in stub_ols["remove"]

    with write_session() as session:
        gone = session.scalar(select(Domain).where(Domain.domain == "addon.example"))
        assert gone is None
        still_there = session.scalar(select(Domain).where(Domain.domain == "demo1.example"))
        assert still_there is not None


def test_remove_domain_restores_row_when_ols_apply_fails(isolated_db, stub_sysops, stub_filesystem, stub_ols, monkeypatch):
    """A failed OLS reload must not turn a retry into a false ownership 403."""
    from api import security

    account = ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    hd.add_domain({"username": "demo1", "domain": "addon.example", "kind": "addon"})

    with write_session() as session:
        before = session.scalar(select(Domain).where(Domain.domain == "addon.example"))
        before_values = {
            column.name: getattr(before, column.name)
            for column in Domain.__table__.columns
        }

    def boom(account_snapshot, domain_name):
        raise RuntimeError("openlitespeed -t failed")

    monkeypatch.setattr(hd.ols, "remove_domain_vhost", boom)
    with pytest.raises(RuntimeError, match="openlitespeed -t failed"):
        hd.remove_domain({"username": "demo1", "domain": "addon.example"})

    with write_session() as session:
        restored = session.scalar(select(Domain).where(Domain.domain == "addon.example"))
        assert restored is not None
        assert {
            column.name: getattr(restored, column.name)
            for column in Domain.__table__.columns
        } == before_values

    # The same owner can retry using a DNS-equivalent spelling; this exercises
    # both the compensation and API ownership normalization fixes.
    identity = security.Identity(1, "custlogin", "customer", account["id"], "session")
    security.require_domain_access(identity, "ADDON.EXAMPLE.")


def test_remove_domain_refuses_primary(isolated_db, stub_sysops, stub_filesystem, stub_ols):
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    with pytest.raises(RuntimeError):
        hd.remove_domain({"username": "demo1", "domain": "demo1.example"})


def test_remove_domain_cleans_up_its_own_dns_record(isolated_db, stub_sysops, stub_filesystem, stub_ols, stub_powerdns, monkeypatch):
    monkeypatch.setattr(hd.settings, "server_public_ip", "203.0.113.10")
    ha.create_account({"username": "demo1"})
    _add_zone_owned_by("demo1", "demo1.example")
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    hd.add_domain({"username": "demo1", "domain": "blog.demo1.example", "kind": "subdomain"})

    hd.remove_domain({"username": "demo1", "domain": "blog.demo1.example"})
    assert ("demo1.example", "blog", "A") in stub_powerdns["delete"]


def test_remove_domain_unknown_domain_raises(isolated_db, stub_sysops, stub_filesystem, stub_ols):
    ha.create_account({"username": "demo1"})
    with pytest.raises(RuntimeError):
        hd.remove_domain({"username": "demo1", "domain": "ghost.example"})


def test_find_parent_zone_matches_exact_and_suffix(isolated_db, stub_sysops):
    ha.create_account({"username": "demo1"})
    _add_zone_owned_by("demo1", "example.com")
    assert hd._find_parent_zone("example.com") == "example.com"
    assert hd._find_parent_zone("blog.example.com") == "example.com"
    assert hd._find_parent_zone("other.example") is None


def test_subdomain_label_computation():
    assert hd._subdomain_label("blog.example.com", "example.com") == "blog"
    assert hd._subdomain_label("example.com", "example.com") == "@"
    assert hd._subdomain_label("a.b.example.com", "example.com") == "a.b"


# --- Phase 7a feature 6: per-domain PHP version override -------------------


def test_set_domain_php_version_sets_override_and_refreshes_vhost(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    calls = []
    monkeypatch.setattr(hd.ols, "refresh_vhost", lambda account: calls.append(account.username))
    monkeypatch.setattr(hd.ols, "provision_vhost", lambda account: None)

    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    result = hd.set_domain_php_version({"username": "demo1", "domain": "demo1.example", "php_version": "8.1"})
    assert result["php_version"] == "8.1"
    assert "demo1" in calls

    with write_session() as session:
        domain = session.scalar(select(Domain).where(Domain.domain == "demo1.example"))
        assert domain.php_version == "8.1"


def test_set_domain_php_version_empty_string_clears_override(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    monkeypatch.setattr(hd.ols, "refresh_vhost", lambda account: None)
    monkeypatch.setattr(hd.ols, "provision_vhost", lambda account: None)

    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})
    hd.set_domain_php_version({"username": "demo1", "domain": "demo1.example", "php_version": "8.1"})

    result = hd.set_domain_php_version({"username": "demo1", "domain": "demo1.example", "php_version": ""})
    assert result["php_version"] is None

    with write_session() as session:
        domain = session.scalar(select(Domain).where(Domain.domain == "demo1.example"))
        assert domain.php_version is None


def test_set_domain_php_version_rejects_unsupported_version(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    monkeypatch.setattr(hd.ols, "provision_vhost", lambda account: None)
    ha.create_account({"username": "demo1"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    with pytest.raises(Exception):
        hd.set_domain_php_version({"username": "demo1", "domain": "demo1.example", "php_version": "5.6"})


def test_set_domain_php_version_rejects_domain_not_owned_by_account(isolated_db, stub_sysops, stub_filesystem, monkeypatch):
    monkeypatch.setattr(hd.ols, "provision_vhost", lambda account: None)
    ha.create_account({"username": "demo1"})
    ha.create_account({"username": "demo2"})
    hd.add_domain({"username": "demo1", "domain": "demo1.example", "kind": "primary"})

    with pytest.raises(RuntimeError):
        hd.set_domain_php_version({"username": "demo2", "domain": "demo1.example", "php_version": "8.1"})
