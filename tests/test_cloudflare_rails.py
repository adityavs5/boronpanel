"""Phase 2+3 features 2/3/4: proxy toggle gate + real-IP rails + fail2ban.

- Feature 3: OLS real-IP render (useIpInProxyHeader + trusted CF ranges),
  cf.refresh_ranges materialization + reload orchestration, cf.rails_status.
- Feature 4: fail2ban ignoreip carries the CF ranges.
- Feature 2: proxied_allowed() is gated on the rails; upsert respects the
  flag once green; cf.enable_proxy flips eligible records only.
All against FakeCF + temp files -- no live OLS/fail2ban/API.
"""
from __future__ import annotations

import json

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from daemon import cloudflare, cloudflare_ops, dnsprovider, fail2ban, ols
from shared.config import settings
from shared.db import write_session
from shared.models import Account, CloudflareZone, DnsZone

from tests.test_cloudflare import FakeCF


@pytest.fixture()
def cf(isolated_db, monkeypatch, tmp_path):
    fake = FakeCF()
    monkeypatch.setattr(cloudflare, "_transport", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(cloudflare.time, "sleep", lambda s: None)
    monkeypatch.setitem(settings.secrets, "CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setattr(settings, "cloudflare_account_id", "")
    monkeypatch.setitem(settings.secrets, "APP_ENV_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.setattr(settings, "cloudflare_ranges_file", str(tmp_path / "cloudflare-ranges.json"))
    return fake


RANGES = ["173.245.48.0/20", "103.21.244.0/22", "2400:cb00::/32"]


def _write_ranges(v4=("173.245.48.0/20", "103.21.244.0/22"), v6=("2400:cb00::/32",)):
    import time
    with open(settings.cloudflare_ranges_file, "w") as f:
        json.dump({"ipv4_cidrs": list(v4), "ipv6_cidrs": list(v6), "etag": "e", "fetched_at": int(time.time())}, f)


# --- feature 3: OLS real-IP render -----------------------------------------


def test_ols_render_emits_real_ip_directives_when_ranges_present():
    conf = ols.render_httpd_config([], [], cloudflare_ranges=["173.245.48.0/20", "2400:cb00::/32"])
    assert "useIpInProxyHeader       2" in conf
    assert "173.245.48.0/20T" in conf
    assert "2400:cb00::/32T" in conf


def test_ols_render_is_unchanged_without_ranges():
    conf = ols.render_httpd_config([], [], cloudflare_ranges=[])
    assert "useIpInProxyHeader" not in conf
    assert "allow                                   ALL\n" in conf  # plain allow, no T-marked ranges


# --- feature 4: fail2ban ignoreip ------------------------------------------


def test_fail2ban_jail_conf_carries_cloudflare_ranges():
    conf = fail2ban._render_jail_conf(RANGES)
    assert "[DEFAULT]" in conf
    ignore_line = next(l for l in conf.splitlines() if l.strip().startswith("ignoreip"))
    assert "127.0.0.1/8" in ignore_line and "::1" in ignore_line
    for cidr in RANGES:
        assert cidr in ignore_line
    # jail definitions still present
    assert "[ols-scan]" in conf and "[postfix]" in conf


def test_fail2ban_jail_conf_base_only_without_ranges():
    conf = fail2ban._render_jail_conf([])
    ignore_line = next(l for l in conf.splitlines() if l.strip().startswith("ignoreip"))
    assert ignore_line.strip() == "ignoreip = 127.0.0.1/8 ::1"


def test_cloudflare_ignoreip_configured(cf, monkeypatch, tmp_path):
    _write_ranges()
    jail_path = tmp_path / "forgehost.conf"
    monkeypatch.setattr(fail2ban, "JAIL_D_PATH", str(jail_path))
    jail_path.write_text(fail2ban._render_jail_conf(fail2ban._cloudflare_ranges()))
    assert fail2ban.cloudflare_ignoreip_configured() is True
    # stale conf (ranges not in it) -> not configured
    jail_path.write_text(fail2ban._render_jail_conf([]))
    assert fail2ban.cloudflare_ignoreip_configured() is False


# --- feature 3: cf.refresh_ranges ------------------------------------------


def test_refresh_ranges_writes_file_and_reloads_on_change(cf, monkeypatch):
    reloads = {"ols": 0, "f2b": 0}
    monkeypatch.setattr(ols, "refresh_main_config", lambda: reloads.__setitem__("ols", reloads["ols"] + 1))
    monkeypatch.setattr(
        fail2ban, "refresh_cloudflare_ignoreip", lambda: (reloads.__setitem__("f2b", reloads["f2b"] + 1), True)[1]
    )
    res = cloudflare_ops.refresh_ranges({})
    assert res["changed"] is True
    assert res["ipv4_count"] == 1 and res["ipv6_count"] == 1  # FakeCF /ips
    assert res["ols_reloaded"] is True and res["fail2ban_updated"] is True
    assert reloads == {"ols": 1, "f2b": 1}
    data = json.loads(open(settings.cloudflare_ranges_file).read())
    assert data["ipv4_cidrs"] == ["173.245.48.0/20"]

    # Second call: unchanged -> no reloads.
    res2 = cloudflare_ops.refresh_ranges({})
    assert res2["changed"] is False
    assert reloads == {"ols": 1, "f2b": 1}


def test_refresh_ranges_force_reloads_even_when_unchanged(cf, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(ols, "refresh_main_config", lambda: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(fail2ban, "refresh_cloudflare_ignoreip", lambda: True)
    cloudflare_ops.refresh_ranges({})
    res = cloudflare_ops.refresh_ranges({"force": True})
    assert res["changed"] is True
    assert calls["n"] == 2


# --- feature 3: cf.rails_status --------------------------------------------


def test_rails_status_ready_only_when_all_green(cf, monkeypatch):
    _write_ranges()
    monkeypatch.setattr(cloudflare_ops, "_ols_real_ip_configured", lambda: True)
    monkeypatch.setattr(fail2ban, "cloudflare_ignoreip_configured", lambda: True)
    assert cloudflare_ops.rails_status({})["ready"] is True

    monkeypatch.setattr(cloudflare_ops, "_ols_real_ip_configured", lambda: False)
    st = cloudflare_ops.rails_status({})
    assert st["ready"] is False
    assert st["ols_real_ip"] is False


def test_rails_status_not_ready_without_ranges(cf, monkeypatch):
    monkeypatch.setattr(cloudflare_ops, "_ols_real_ip_configured", lambda: True)
    monkeypatch.setattr(fail2ban, "cloudflare_ignoreip_configured", lambda: True)
    assert cloudflare_ops.rails_status({})["ready"] is False  # no ranges file


# --- feature 2: proxied gate + enable_proxy --------------------------------


def _active_zone(cf: FakeCF) -> str:
    zone = "proxy.com"
    zid = cf.add_zone(zone, status="active")
    cf.add_record(zid, type="A", name=zone, content="192.0.2.1")
    cf.add_record(zid, type="A", name=f"www.{zone}", content="192.0.2.2")
    cf.add_record(zid, type="MX", name=zone, content="mail.example.com", priority=10)
    cf.add_record(zid, type="TXT", name=zone, content="v=spf1 -all")
    with write_session() as session:
        acc = Account(username="p", status="active")
        session.add(acc)
        session.flush()
        session.add(DnsZone(account_id=acc.id, zone=zone))
        session.add(
            CloudflareZone(
                account_id=acc.id, zone=zone, cf_zone_id=zid, status="active", name_servers=[], cf_account_id=None
            )
        )
    return zone


def test_proxied_allowed_follows_rails(cf, monkeypatch):
    monkeypatch.setattr(cloudflare_ops, "rails_status", lambda p: {"ready": True})
    assert dnsprovider.proxied_allowed() is True
    monkeypatch.setattr(cloudflare_ops, "rails_status", lambda p: {"ready": False})
    assert dnsprovider.proxied_allowed() is False


def test_upsert_respects_proxied_once_rails_green(cf, monkeypatch):
    zone = _active_zone(cf)
    monkeypatch.setattr(dnsprovider, "proxied_allowed", lambda: True)
    dnsprovider.upsert_record(zone, "app", "A", ["192.0.2.9"], proxied=True)
    recs = cloudflare.list_records(zone)
    app = next(r for r in recs if r["name"] == f"app.{zone}")
    assert app["proxied"] is True


def test_upsert_forces_grey_when_rails_red(cf, monkeypatch):
    zone = _active_zone(cf)
    monkeypatch.setattr(dnsprovider, "proxied_allowed", lambda: False)
    dnsprovider.upsert_record(zone, "app2", "A", ["192.0.2.8"], proxied=True)
    recs = cloudflare.list_records(zone)
    app = next(r for r in recs if r["name"] == f"app2.{zone}")
    assert app["proxied"] is False


def test_enable_proxy_flips_eligible_records_only(cf, monkeypatch):
    zone = _active_zone(cf)
    monkeypatch.setattr(dnsprovider, "proxied_allowed", lambda: True)
    res = cloudflare_ops.enable_proxy({"domain": zone})
    assert res["proxied_records"] == 2  # the two A records; MX/TXT skipped
    recs = {(r["name"], r["type"]): r for r in cloudflare.list_records(zone)}
    assert recs[(zone, "A")]["proxied"] is True
    assert recs[(f"www.{zone}", "A")]["proxied"] is True
    assert recs[(zone, "MX")]["proxied"] is False
    assert recs[(zone, "TXT")]["proxied"] is False


def test_enable_proxy_refused_when_rails_red(cf, monkeypatch):
    zone = _active_zone(cf)
    monkeypatch.setattr(dnsprovider, "proxied_allowed", lambda: False)
    with pytest.raises(RuntimeError, match="rails"):
        cloudflare_ops.enable_proxy({"domain": zone})


# --- feature 5: SSL mode upgrade on cert deploy ----------------------------


def test_upgrade_ssl_strict_sets_strict_for_active_zone(cf):
    zone = _active_zone(cf)
    zid = next(z for z in cf.zones if cf.zones[z]["name"] == zone)
    assert cloudflare_ops.upgrade_ssl_strict(zone) is True
    assert cf.zones[zid]["ssl_mode"] == "strict"


def test_upgrade_ssl_strict_noop_for_non_cf_domain(cf):
    assert cloudflare_ops.upgrade_ssl_strict("not-a-cf-zone.com") is False
