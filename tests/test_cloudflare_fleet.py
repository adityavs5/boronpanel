"""Phase 2+3 features 6/7/8/9: auto-enable, bulk migrate, zone overview,
UFW CF-only lockdown.

Against FakeCF + FakePDNS + isolated DB; UFW is stubbed (no real firewall).
"""
from __future__ import annotations

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from daemon import cloudflare, cloudflare_accounts, cloudflare_ops, firewall, handlers_dns, ols, powerdns
from shared.config import settings
from shared.db import write_session
from shared.models import Account, CloudflareSettings, CloudflareZone, DnsZone, Domain

from tests.test_cloudflare import FakeCF
from tests.test_cloudflare_zones import FakePDNS


@pytest.fixture()
def cf(isolated_db, monkeypatch, tmp_path):
    fake = FakeCF()
    monkeypatch.setattr(cloudflare, "_transport", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(cloudflare.time, "sleep", lambda s: None)
    monkeypatch.setitem(settings.secrets, "CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setattr(settings, "cloudflare_account_id", "acct-1")
    monkeypatch.setitem(settings.secrets, "APP_ENV_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setattr(settings, "cloudflare_ranges_file", str(tmp_path / "ranges.json"))
    return fake


@pytest.fixture()
def fake_pdns(monkeypatch):
    fake = FakePDNS()
    for name in ("zone_exists", "create_zone", "delete_zone", "upsert_record", "delete_record", "list_records"):
        monkeypatch.setattr(powerdns, name, getattr(fake, name))
    return fake


def _account_and_domain(zone="site.com", username="site"):
    with write_session() as session:
        acc = Account(username=username, status="active", primary_domain=zone)
        session.add(acc)
        session.flush()
        session.add(Domain(account_id=acc.id, domain=zone, kind="primary", docroot=f"/home/{username}/public_html"))
        return acc.id


# --- feature 6: auto-enable -------------------------------------------------


def test_create_zone_auto_enables_when_toggle_on(cf, fake_pdns, monkeypatch):
    monkeypatch.setattr(settings, "server_public_ip", "192.0.2.50")
    acc_id = _account_and_domain()
    with write_session() as session:
        session.add(CloudflareSettings(id=1, auto_enable=True))

    result = handlers_dns.create_zone({"domain": "site.com", "username": "site"})
    assert "cloudflare" in result
    assert len(result["cloudflare"]["name_servers"]) == 2
    with write_session() as session:
        assert session.scalar(select(CloudflareZone).where(CloudflareZone.zone == "site.com")) is not None


def test_create_zone_no_auto_enable_when_off(cf, fake_pdns, monkeypatch):
    monkeypatch.setattr(settings, "server_public_ip", "192.0.2.50")
    _account_and_domain()
    result = handlers_dns.create_zone({"domain": "site.com", "username": "site"})
    assert "cloudflare" not in result
    with write_session() as session:
        assert session.scalar(select(CloudflareZone).where(CloudflareZone.zone == "site.com")) is None


def test_auto_enable_skipped_without_capacity(cf, fake_pdns, monkeypatch):
    # auto_enable on, but a full pool + no legacy token -> skip cleanly.
    monkeypatch.setattr(settings, "server_public_ip", "192.0.2.50")
    monkeypatch.setitem(settings.secrets, "CLOUDFLARE_API_TOKEN", "")  # no legacy fallback
    _account_and_domain()
    with write_session() as session:
        session.add(CloudflareSettings(id=1, auto_enable=True))
    cloudflare_accounts.add_account({"name": "a", "api_token": "t", "account_id": "cf-a", "max_zones": 1})
    with write_session() as session:
        session.scalar(select(cloudflare_accounts.CloudflareAccount)).zone_count = 1  # full
    result = handlers_dns.create_zone({"domain": "site.com", "username": "site"})
    assert "cloudflare" not in result  # skipped, zone still created locally


def test_settings_get_set_roundtrip(cf):
    assert cloudflare_ops.settings_get({})["auto_enable"] is False
    cloudflare_ops.settings_set({"auto_enable": True})
    assert cloudflare_ops.settings_get({})["auto_enable"] is True


# --- feature 7: bulk migrate ------------------------------------------------


def test_bulk_migrate_prepares_local_zones(cf, fake_pdns):
    for z in ("one.com", "two.com"):
        with write_session() as session:
            acc = Account(username=z.split(".")[0], status="active")
            session.add(acc)
            session.flush()
            session.add(DnsZone(account_id=acc.id, zone=z))
        fake_pdns.create_zone(z, [f"ns1.{z}.", f"ns2.{z}."])
        fake_pdns.upsert_record(z, "@", "A", ["192.0.2.1"])

    res = cloudflare_ops.bulk_migrate({})
    assert res["prepared"] == 2
    assert res["stopped_on_error"] is False
    for r in res["results"]:
        assert r["status"] == "prepared"
        assert len(r["name_servers"]) == 2
    with write_session() as session:
        assert session.scalar(select(CloudflareZone).where(CloudflareZone.zone == "one.com")) is not None


def test_bulk_migrate_skips_already_cf_and_respects_limit(cf, fake_pdns):
    for z in ("a.com", "b.com", "c.com"):
        with write_session() as session:
            acc = Account(username=z[0], status="active")
            session.add(acc)
            session.flush()
            session.add(DnsZone(account_id=acc.id, zone=z))
        fake_pdns.create_zone(z, [f"ns1.{z}.", f"ns2.{z}."])
    # a.com already on CF
    with write_session() as session:
        acc = session.scalar(select(Account).where(Account.username == "a"))
        session.add(CloudflareZone(account_id=acc.id, zone="a.com", cf_zone_id="zx", status="active", name_servers=[]))

    res = cloudflare_ops.bulk_migrate({"limit": 1})
    assert res["attempted"] == 1  # only one attempted due to limit
    assert res["results"][0]["zone"] in ("b.com", "c.com")  # a.com skipped (already CF)


# --- feature 8: zone overview + bulk purge ----------------------------------


def test_zones_overview_reports_fleet(cf, fake_pdns):
    a = cloudflare_accounts.add_account({"name": "pool-a", "api_token": "t", "account_id": "cf-a"})
    with write_session() as session:
        acc = Account(username="ov", status="active")
        session.add(acc)
        session.flush()
        session.add(
            CloudflareZone(
                account_id=acc.id, zone="ov.com", cf_zone_id="zid", status="active",
                name_servers=["x.ns.cloudflare.com"], cf_account_id=a["id"],
            )
        )
    ov = cloudflare_ops.zones_overview({})
    assert ov["count"] == 1
    z = ov["zones"][0]
    assert z["zone"] == "ov.com"
    assert z["status"] == "active"
    assert z["cf_account"] == "pool-a"
    assert z["hosting_account"] == "ov"


def test_zones_overview_live_reports_proxy_and_ssl_mode(cf):
    zid = cf.add_zone("live.com", status="active")
    cf.add_record(zid, type="A", name="live.com", content="192.0.2.1", proxied=True)
    cf.zones[zid]["ssl_mode"] = "strict"
    with write_session() as session:
        acc = Account(username="lv", status="active")
        session.add(acc)
        session.flush()
        session.add(
            CloudflareZone(account_id=acc.id, zone="live.com", cf_zone_id=zid, status="active", name_servers=[])
        )
    ov = cloudflare_ops.zones_overview({"live": True})
    z = ov["zones"][0]
    assert z["proxied"] is True
    assert z["ssl_mode"] == "strict"


def test_bulk_purge_purges_active_zones(cf):
    zid = cf.add_zone("purge.com", status="active")
    with write_session() as session:
        acc = Account(username="pg", status="active")
        session.add(acc)
        session.flush()
        session.add(
            CloudflareZone(account_id=acc.id, zone="purge.com", cf_zone_id=zid, status="active", name_servers=[])
        )
    res = cloudflare_ops.bulk_purge({})
    assert res["purged"] == 1
    assert cf.zones[zid]["purged"] is True
    # last_purge_at recorded
    with write_session() as session:
        assert session.scalar(select(CloudflareZone).where(CloudflareZone.zone == "purge.com")).last_purge_at is not None


# --- feature 9: UFW lockdown ------------------------------------------------


@pytest.fixture()
def fake_ufw(monkeypatch):
    """In-memory UFW: records added rules, serves them back via show added."""
    state = {"rules": []}  # each: full arg list after 'ufw'

    def fake_run(args, timeout=15):
        from daemon.procutil import ProcResult

        assert args[0] == "ufw"
        rest = args[1:]
        if rest[:2] == ["show", "added"]:
            lines = ["Added user rules (see 'ufw status' for running firewall):"]
            lines += ["ufw " + " ".join(_quote_comment(r)) for r in state["rules"]]
            return ProcResult(args=args, returncode=0, stdout="\n".join(lines) + "\n", stderr="")
        if rest[0] == "--force" and rest[1] == "delete":
            spec = rest[2:]
            # real `ufw delete` matches on the rule spec, ignoring any comment
            state["rules"] = [r for r in state["rules"] if _strip_comment(r) != spec]
            return ProcResult(args=args, returncode=0, stdout="", stderr="")
        # an add rule
        state["rules"].append(rest)
        return ProcResult(args=args, returncode=0, stdout="", stderr="")

    def _strip_comment(rule):
        return rule[: rule.index("comment")] if "comment" in rule else rule

    def _quote_comment(rule):
        out = []
        i = 0
        while i < len(rule):
            if rule[i] == "comment" and i + 1 < len(rule):
                out += ["comment", f"'{rule[i + 1]}'"]
                i += 2
            else:
                out.append(rule[i])
                i += 1
        return out

    monkeypatch.setattr(firewall, "run", fake_run)
    return state


def test_lockdown_requires_confirm(cf, fake_ufw):
    with pytest.raises(RuntimeError, match="confirm"):
        cloudflare_ops.lockdown({"enabled": True})


def test_lockdown_refuses_without_ranges(cf, fake_ufw):
    with pytest.raises(RuntimeError, match="ranges"):
        cloudflare_ops.lockdown({"enabled": True, "confirm": True})


def test_lockdown_refuses_with_unproxied_domain(cf, fake_ufw, monkeypatch):
    _write_ranges()
    _account_and_domain()  # a Domain with no active CF zone
    with pytest.raises(RuntimeError, match="unreachable"):
        cloudflare_ops.lockdown({"enabled": True, "confirm": True})


def test_lockdown_enable_and_disable(cf, fake_ufw, monkeypatch):
    _write_ranges()
    # force past the safety check (no domains anyway)
    res = cloudflare_ops.lockdown({"enabled": True, "confirm": True, "force": True})
    assert res["lockdown_enabled"] is True
    assert firewall.cf_lockdown_active() is True
    # scoped rules added for 80 and 443, general allow removed
    rules = fake_ufw["rules"]
    assert any(r[:2] == ["allow", "from"] and "80" in r for r in rules)
    assert any(r[:2] == ["allow", "from"] and "443" in r for r in rules)
    with write_session() as session:
        assert session.get(CloudflareSettings, 1).lockdown_enabled is True

    cloudflare_ops.lockdown({"enabled": False})
    assert firewall.cf_lockdown_active() is False
    with write_session() as session:
        assert session.get(CloudflareSettings, 1).lockdown_enabled is False


def test_lockdown_keeps_general_allow_if_scoped_adds_fail(cf, monkeypatch):
    """Safety: if the scoped CF allows can't be added, the general
    allow-from-any for 80/443 must NOT be removed (never blackhole the web)."""
    from daemon.procutil import ProcResult

    rules = [["allow", "80"], ["allow", "443"]]  # pre-existing general allows

    def fake_run(args, timeout=15):
        rest = args[1:]
        if rest[:2] == ["show", "added"]:
            lines = ["Added user rules (see 'ufw status' for running firewall):"]
            lines += ["ufw " + " ".join(r) for r in rules]
            return ProcResult(args=args, returncode=0, stdout="\n".join(lines) + "\n", stderr="")
        if rest[0] == "--force" and rest[1] == "delete":
            spec = rest[2:]
            rules[:] = [r for r in rules if r != spec]
            return ProcResult(args=args, returncode=0, stdout="", stderr="")
        # an add of a scoped rule -> simulate ufw FAILURE (rule not added)
        return ProcResult(args=args, returncode=1, stdout="", stderr="ERROR: could not add rule")

    monkeypatch.setattr(firewall, "run", fake_run)
    with pytest.raises(RuntimeError, match="could not add rule"):
        firewall.apply_cf_lockdown(["173.245.48.0/20"])
    # general allows for 80 and 443 must still be present
    assert ["allow", "80"] in rules
    assert ["allow", "443"] in rules


def _write_ranges():
    import json
    import time
    with open(settings.cloudflare_ranges_file, "w") as f:
        json.dump({"ipv4_cidrs": ["173.245.48.0/20"], "ipv6_cidrs": ["2400:cb00::/32"], "fetched_at": int(time.time())}, f)


@pytest.fixture(autouse=True)
def isolated_firewall_ssh_ports(monkeypatch):
    monkeypatch.setattr(firewall, '_ssh_ports', lambda: {22})
