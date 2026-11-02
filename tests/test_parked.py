"""Phase 8 feature 3: parked (alias) domains."""
import pytest
from sqlalchemy import select

from daemon import parked
from shared.db import write_session
from shared.models import Account, DnsZone, Domain, ParkedDomain


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    """Zone lifecycle and record calls, recorded instead of sent to PowerDNS."""
    calls = {"zones": [], "deleted_zones": [], "records": [], "deleted_records": []}
    monkeypatch.setattr(parked.handlers_domain, "_create_managed_zone",
                        lambda username, domain: calls["zones"].append(domain) or {"zone": domain})
    monkeypatch.setattr(parked.handlers_domain, "_delete_managed_zone",
                        lambda domain: calls["deleted_zones"].append(domain) or {"zone": domain, "status": "deleted"})
    monkeypatch.setattr(parked.dnsprovider, "upsert_record",
                        lambda zone, label, rtype, values, ttl=None, proxied=False:
                        calls["records"].append((zone, label, rtype, tuple(values))))
    monkeypatch.setattr(parked.dnsprovider, "delete_record",
                        lambda zone, label, rtype: calls["deleted_records"].append((zone, label, rtype)))
    return calls


def _setup(monkeypatch, username="demo1"):
    monkeypatch.setattr(parked.ols, "provision_vhost", lambda a: None)
    monkeypatch.setattr(parked.ols, "remove_domain_vhost", lambda a, d: None)
    with write_session() as db:
        account = Account(username=username, status="active", uid=5001, gid=5001, primary_domain="site.com")
        db.add(account)
        db.flush()
        db.add(Domain(account_id=account.id, domain="site.com", kind="primary", docroot="/home/demo1/public_html"))
        return account.id


def test_add_parked_defaults_to_primary(isolated_db, monkeypatch):
    account_id = _setup(monkeypatch)
    result = parked.add_parked_domain({"username": "demo1", "parked_domain": "alias.com"})
    assert result["target_domain"] == "site.com"
    assert result["docroot"] == "/home/demo1/public_html"
    with write_session() as db:
        d = db.scalar(select(Domain).where(Domain.domain == "alias.com"))
        assert d.kind == "parked"
        assert d.docroot == "/home/demo1/public_html"  # SAME docroot as target
        p = db.scalar(select(ParkedDomain).where(ParkedDomain.parked_domain == "alias.com"))
        assert p.target_domain == "site.com"


def test_add_parked_explicit_target(isolated_db, monkeypatch):
    account_id = _setup(monkeypatch)
    with write_session() as db:
        db.add(Domain(account_id=account_id, domain="addon.com", kind="addon", docroot="/home/demo1/addon.com"))
    result = parked.add_parked_domain({"username": "demo1", "parked_domain": "alias.com", "target_domain": "addon.com"})
    assert result["docroot"] == "/home/demo1/addon.com"


def test_add_parked_rejects_used_domain(isolated_db, monkeypatch):
    _setup(monkeypatch)
    with pytest.raises(RuntimeError, match="already in use"):
        parked.add_parked_domain({"username": "demo1", "parked_domain": "site.com"})


def test_add_parked_rejects_park_on_park(isolated_db, monkeypatch):
    _setup(monkeypatch)
    parked.add_parked_domain({"username": "demo1", "parked_domain": "alias1.com"})
    with pytest.raises(parked.ValidationError, match="onto another parked"):
        parked.add_parked_domain({"username": "demo1", "parked_domain": "alias2.com", "target_domain": "alias1.com"})


def test_list_parked(isolated_db, monkeypatch):
    _setup(monkeypatch)
    parked.add_parked_domain({"username": "demo1", "parked_domain": "a.com"})
    parked.add_parked_domain({"username": "demo1", "parked_domain": "b.com"})
    result = parked.list_parked_domains({"username": "demo1"})
    names = {p["parked_domain"] for p in result["parked_domains"]}
    assert names == {"a.com", "b.com"}


def test_parked_domain_reports_target_suspension(isolated_db, monkeypatch):
    _setup(monkeypatch)
    parked.add_parked_domain({"username":"demo1","parked_domain":"alias.com"})
    with write_session() as db:
        target=db.scalar(select(Domain).where(Domain.domain=="site.com"))
        target.suspended=True;target.suspension_reason="Maintenance window"
    item=parked.list_parked_domains({"username":"demo1"})["parked_domains"][0]
    assert item["effective_suspended"] is True
    assert item["suspension_reason"]=="Maintenance window"


def test_remove_parked(isolated_db, monkeypatch):
    account_id = _setup(monkeypatch)
    parked.add_parked_domain({"username": "demo1", "parked_domain": "alias.com"})
    parked.remove_parked_domain({"username": "demo1", "parked_domain": "alias.com"})
    with write_session() as db:
        assert db.scalar(select(Domain).where(Domain.domain == "alias.com")) is None
        assert db.scalar(select(ParkedDomain).where(ParkedDomain.parked_domain == "alias.com")) is None


def test_add_parked_rolls_back_on_ols_failure(isolated_db, monkeypatch):
    account_id = _setup(monkeypatch)

    def boom(a):
        raise RuntimeError("ols failed")

    monkeypatch.setattr(parked.ols, "provision_vhost", boom)
    with pytest.raises(RuntimeError, match="ols failed"):
        parked.add_parked_domain({"username": "demo1", "parked_domain": "alias.com"})
    # Rolled back: no Domain and no ParkedDomain row left behind.
    with write_session() as db:
        assert db.scalar(select(Domain).where(Domain.domain == "alias.com")) is None
        assert db.scalar(select(ParkedDomain).where(ParkedDomain.parked_domain == "alias.com")) is None


def test_terminate_cleans_parked_rows(isolated_db, monkeypatch):
    account_id = _setup(monkeypatch)
    parked.add_parked_domain({"username": "demo1", "parked_domain": "alias.com"})
    with write_session() as db:
        account = db.get(Account, account_id)
        parked.terminate_account_parked(account)
    with write_session() as db:
        assert db.scalar(select(ParkedDomain).where(ParkedDomain.account_id == account_id)) is None


# --- DNS like an addon domain, removal like domain.remove (live findings) ---


def test_parked_domain_outside_every_managed_zone_gets_its_own_zone(isolated_db, monkeypatch, dns):
    """Live finding: a parked name outside a managed parent zone got no DNS
    at all (dns_record_created false, nothing answered), while an addon
    domain at the same level gets a zone."""
    _setup(monkeypatch)
    result = parked.add_parked_domain({"username": "demo1", "parked_domain": "alias.com"})
    assert result["dns_zone_created"] is True and result["dns_record_created"] is False
    assert dns["zones"] == ["alias.com"]


def test_parked_domain_in_a_managed_zone_uses_the_accounts_address(isolated_db, monkeypatch, dns):
    from daemon import ipmanager

    account_id = _setup(monkeypatch)
    with write_session() as db:
        db.add(DnsZone(account_id=account_id, zone="site.com"))
    monkeypatch.setattr(ipmanager, "address_for_account", lambda account: "2001:db8::5")
    result = parked.add_parked_domain({"username": "demo1", "parked_domain": "shop.site.com"})
    assert result["dns_record_created"] is True and result["dns_zone_created"] is False
    assert dns["records"] == [("site.com", "shop", "AAAA", ("2001:db8::5",))]
    assert dns["zones"] == []


def test_failed_parked_add_rolls_back_the_zone_it_created(isolated_db, monkeypatch, dns):
    _setup(monkeypatch)
    monkeypatch.setattr(parked.ols, "provision_vhost", lambda a: (_ for _ in ()).throw(RuntimeError("ols failed")))
    with pytest.raises(RuntimeError, match="ols failed"):
        parked.add_parked_domain({"username": "demo1", "parked_domain": "alias.com"})
    assert dns["zones"] == ["alias.com"] and dns["deleted_zones"] == ["alias.com"]
    with write_session() as db:
        assert db.scalar(select(Domain).where(Domain.domain == "alias.com")) is None


def test_parking_another_accounts_domain_is_a_conflict(isolated_db, monkeypatch):
    """Live finding UW L2: this returned 502."""
    from daemon import server
    from shared.validation import ConflictError

    _setup(monkeypatch)
    with write_session() as db:
        other = Account(username="other1", status="active", uid=5002, gid=5002)
        db.add(other)
        db.flush()
        db.add(Domain(account_id=other.id, domain="theirs.com", kind="primary", docroot="/home/other1/public_html"))
        db.add(DnsZone(account_id=other.id, zone="theirs.com"))
    with pytest.raises(ConflictError, match="already belongs to another account") as raised:
        parked.add_parked_domain({"username": "demo1", "parked_domain": "theirs.com"})
    assert server.rpc_error_code(raised.value) == "conflict"


def test_remove_parked_deletes_its_zone_and_name_keyed_settings(isolated_db, monkeypatch, dns):
    from shared.models import Redirect, StagingEnvironment

    account_id = _setup(monkeypatch)
    parked.add_parked_domain({"username": "demo1", "parked_domain": "alias.com"})
    with write_session() as db:
        db.add(DnsZone(account_id=account_id, zone="alias.com"))  # what handlers_dns.create_zone records
        db.add(Redirect(domain="alias.com", path="/old", target_url="https://example.org/new", status_code=301))
        db.add(StagingEnvironment(account_id=account_id, source_domain="alias.com",
                                  staging_domain="staging.alias.com", is_wordpress=False))
    result = parked.remove_parked_domain({"username": "demo1", "parked_domain": "alias.com"})
    assert result == {"parked_domain": "alias.com", "status": "removed"}
    assert dns["deleted_zones"] == ["alias.com"]
    with write_session() as db:
        assert db.scalars(select(Redirect)).all() == []
        assert db.scalars(select(StagingEnvironment)).all() == []


def test_remove_parked_in_a_parent_zone_deletes_both_address_families(isolated_db, monkeypatch, dns):
    account_id = _setup(monkeypatch)
    with write_session() as db:
        db.add(DnsZone(account_id=account_id, zone="site.com"))
    parked.add_parked_domain({"username": "demo1", "parked_domain": "shop.site.com"})
    parked.remove_parked_domain({"username": "demo1", "parked_domain": "shop.site.com"})
    assert dns["deleted_records"] == [("site.com", "shop", "A"), ("site.com", "shop", "AAAA")]


def test_remove_parked_restores_rows_when_ols_rejects(isolated_db, monkeypatch, dns):
    _setup(monkeypatch)
    parked.add_parked_domain({"username": "demo1", "parked_domain": "alias.com"})
    monkeypatch.setattr(parked.ols, "remove_domain_vhost", lambda a, d: (_ for _ in ()).throw(RuntimeError("ols failed")))
    with pytest.raises(RuntimeError, match="ols failed"):
        parked.remove_parked_domain({"username": "demo1", "parked_domain": "alias.com"})
    assert dns["deleted_zones"] == [] and dns["deleted_records"] == []
    with write_session() as db:
        assert db.scalar(select(Domain).where(Domain.domain == "alias.com")).kind == "parked"
        assert db.scalar(select(ParkedDomain).where(ParkedDomain.parked_domain == "alias.com")) is not None
    assert parked.list_parked_domains({"username": "demo1"})["parked_domains"][0]["parked_domain"] == "alias.com"


def test_remove_parked_busy_dns_lock_changes_nothing(isolated_db, monkeypatch):
    from contextlib import contextmanager
    from shared.validation import BusyError

    _setup(monkeypatch)
    parked.add_parked_domain({"username": "demo1", "parked_domain": "alias.com"})

    @contextmanager
    def busy(**kwargs):
        raise BusyError("A DNS backup, restore or management operation is in progress. Try again shortly.")
        yield

    monkeypatch.setattr(parked.dns_operations, "mutation_lock", busy)
    with pytest.raises(BusyError):
        parked.remove_parked_domain({"username": "demo1", "parked_domain": "alias.com"})
    with write_session() as db:
        assert db.scalar(select(ParkedDomain).where(ParkedDomain.parked_domain == "alias.com")) is not None
