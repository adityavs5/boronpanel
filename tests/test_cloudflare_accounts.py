"""Phase 2+3 feature 1: Cloudflare account pool (multi-account).

Covers CRUD (add live-verifies + encrypts the token, list hides it, delete
guards assigned zones, set mutates), round-robin capacity-aware assignment,
the legacy single-token fallback + startup migration, and end-to-end that
cf.zone_enable places a zone on a pool account, keeps zone_count in step,
and routes each zone's Cloudflare calls through that account's own token.
All against the in-memory FakeCF -- no live calls.
"""
from __future__ import annotations

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from daemon import appcrypto, cloudflare, cloudflare_accounts, cloudflare_ops, powerdns
from shared.config import settings
from shared.db import write_session
from shared.models import Account, CloudflareAccount, CloudflareZone, DnsZone

from tests.test_cloudflare import FakeCF, _err
from tests.test_cloudflare_zones import FakePDNS


class RecordingCF(FakeCF):
    """FakeCF that also records the Bearer token each request carried, so a
    multi-account test can prove per-zone calls use the right account token."""

    def __init__(self):
        super().__init__()
        self.auth_calls: list[tuple[str, str, str]] = []  # (authorization, method, path)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.auth_calls.append(
            (request.headers.get("authorization", ""), request.method, request.url.path.removeprefix("/client/v4"))
        )
        return super().handler(request)


@pytest.fixture()
def cf(isolated_db, monkeypatch):
    fake = RecordingCF()
    monkeypatch.setattr(cloudflare, "_transport", httpx.MockTransport(fake.handler))
    monkeypatch.setattr(cloudflare.time, "sleep", lambda s: None)
    # Pool-only by default: no legacy single token unless a test opts in.
    monkeypatch.setitem(settings.secrets, "CLOUDFLARE_API_TOKEN", "")
    monkeypatch.setattr(settings, "cloudflare_account_id", "")
    # A real Fernet key so appcrypto encrypts in-process without persisting
    # anything to secrets.env.
    monkeypatch.setitem(settings.secrets, "APP_ENV_KEY", Fernet.generate_key().decode("ascii"))
    return fake


@pytest.fixture()
def fake_pdns(monkeypatch):
    fake = FakePDNS()
    for name in ("zone_exists", "create_zone", "delete_zone", "upsert_record", "delete_record", "list_records"):
        monkeypatch.setattr(powerdns, name, getattr(fake, name))
    return fake


def _add(name="acct-a", token="tok-a", account_id="cf-a", max_zones=800):
    return cloudflare_accounts.add_account(
        {"name": name, "api_token": token, "account_id": account_id, "max_zones": max_zones}
    )


def _managed_zone(zone: str, fake_pdns: FakePDNS) -> None:
    with write_session() as session:
        account = Account(username=zone.split(".")[0][:16], status="active", primary_domain=zone)
        session.add(account)
        session.flush()
        session.add(DnsZone(account_id=account.id, zone=zone))
    fake_pdns.create_zone(zone, [f"ns1.{zone}.", f"ns2.{zone}."])
    fake_pdns.upsert_record(zone, "@", "A", ["192.0.2.10"])


# --- add / encryption -------------------------------------------------------


def test_add_account_encrypts_token_and_hides_it(cf):
    res = _add(token="super-secret")
    assert res["name"] == "acct-a"
    assert res["account_id"] == "cf-a"
    assert "api_token" not in res and "token" not in res and "api_token_enc" not in res
    with write_session() as session:
        row = session.scalar(select(CloudflareAccount).where(CloudflareAccount.name == "acct-a"))
        assert row.api_token_enc != "super-secret"  # stored encrypted, not plaintext
        assert appcrypto.decrypt_secret(row.api_token_enc) == "super-secret"


def test_add_account_rejects_inactive_token(cf):
    cf.fail_next.append(_err(403, 9109, "invalid token"))
    with pytest.raises(cloudflare_accounts.CloudflareAccountError):
        _add()
    assert cloudflare_accounts.list_accounts({})["accounts"] == []  # nothing persisted


def test_add_account_requires_account_id(cf):
    with pytest.raises(cloudflare_accounts.CloudflareAccountError):
        cloudflare_accounts.add_account({"name": "x", "api_token": "t", "account_id": ""})


def test_add_account_rejects_duplicate_name(cf):
    _add(name="dup")
    with pytest.raises(cloudflare_accounts.CloudflareAccountError):
        _add(name="dup", account_id="cf-b", token="tok-b")


def test_list_accounts_reports_capacity_without_token(cf):
    _add(name="a", max_zones=10)
    listing = cloudflare_accounts.list_accounts({})
    acct = listing["accounts"][0]
    assert acct["max_zones"] == 10
    assert acct["capacity_remaining"] == 10
    assert acct["full"] is False
    assert "api_token_enc" not in acct


# --- assignment (round-robin, capacity, fallbacks) --------------------------


def test_assign_picks_lowest_zone_count(cf):
    _add(name="a", token="tok-a", account_id="cf-a")
    _add(name="b", token="tok-b", account_id="cf-b")
    # Give a some load; b should win.
    with write_session() as session:
        session.scalar(select(CloudflareAccount).where(CloudflareAccount.name == "a")).zone_count = 5
    pick, reason = cloudflare_accounts.assign_for_new_zone()
    assert reason is None
    assert pick["account_id"] == "cf-b"


def test_assign_skips_full_and_inactive(cf):
    _add(name="full", max_zones=1)
    _add(name="off", account_id="cf-off", token="tok-off")
    with write_session() as session:
        session.scalar(select(CloudflareAccount).where(CloudflareAccount.name == "full")).zone_count = 1
        session.scalar(select(CloudflareAccount).where(CloudflareAccount.name == "off")).active = False
    pick, reason = cloudflare_accounts.assign_for_new_zone()
    assert pick is None
    assert "capacity" in reason


def test_assign_falls_back_to_legacy_token(cf, monkeypatch):
    monkeypatch.setitem(settings.secrets, "CLOUDFLARE_API_TOKEN", "legacy")
    pick, reason = cloudflare_accounts.assign_for_new_zone()
    assert pick is None and reason is None  # (None, None) == use legacy single token


def test_assign_reports_nothing_configured(cf):
    pick, reason = cloudflare_accounts.assign_for_new_zone()
    assert pick is None
    assert "not configured" in reason


# --- delete / set -----------------------------------------------------------


def test_delete_account_refuses_while_serving_zones(cf):
    aid = _add()["id"]
    with write_session() as session:
        acc = Account(username="z", status="active")
        session.add(acc)
        session.flush()
        session.add(
            CloudflareZone(
                account_id=acc.id, zone="z.com", cf_zone_id="z1", status="active",
                name_servers=[], cf_account_id=aid,
            )
        )
    with pytest.raises(cloudflare_accounts.CloudflareAccountError):
        cloudflare_accounts.delete_account({"id": aid})
    # force overrides
    assert cloudflare_accounts.delete_account({"id": aid, "force": True})["status"] == "deleted"


def test_set_account_updates_max_zones_and_active(cf):
    aid = _add()["id"]
    res = cloudflare_accounts.set_account({"id": aid, "max_zones": 42, "active": False})
    assert res["max_zones"] == 42
    assert res["active"] is False


def test_test_account_live_checks_token(cf):
    aid = _add()["id"]
    res = cloudflare_accounts.test_account({"id": aid})
    assert res["token_valid"] is True
    assert res["api_ok"] is True
    assert res["ok"] is True


# --- migration of the legacy single token -----------------------------------


def test_migrate_single_token_creates_first_row_and_adopts_zones(cf, monkeypatch):
    monkeypatch.setitem(settings.secrets, "CLOUDFLARE_API_TOKEN", "legacy-tok")
    monkeypatch.setattr(settings, "cloudflare_account_id", "cf-legacy")
    with write_session() as session:
        acc = Account(username="u", status="active")
        session.add(acc)
        session.flush()
        session.add(
            CloudflareZone(
                account_id=acc.id, zone="old.com", cf_zone_id="z9", status="active",
                name_servers=[], cf_account_id=None,
            )
        )
    assert cloudflare_accounts.migrate_single_token() is True
    accts = cloudflare_accounts.list_accounts({})["accounts"]
    assert len(accts) == 1
    assert accts[0]["name"] == "default"
    assert accts[0]["account_id"] == "cf-legacy"
    assert accts[0]["zone_count"] == 1  # adopted the NULL-account zone
    assert cloudflare_accounts.token_for_id(accts[0]["id"]) == "legacy-tok"
    # idempotent
    assert cloudflare_accounts.migrate_single_token() is False


def test_migrate_noop_without_legacy_token(cf):
    assert cloudflare_accounts.migrate_single_token() is False


# --- end-to-end via cf.zone_enable ------------------------------------------


def test_zone_enable_assigns_pool_account_and_routes_token(cf, fake_pdns):
    a = _add(name="a", token="tok-a", account_id="cf-a")
    b = _add(name="b", token="tok-b", account_id="cf-b")
    _managed_zone("aaa.com", fake_pdns)
    _managed_zone("bbb.com", fake_pdns)

    cloudflare_ops.zone_enable({"domain": "aaa.com"})  # both at 0 -> lowest id (a)
    cloudflare_ops.zone_enable({"domain": "bbb.com"})  # a now at 1 -> b

    with write_session() as session:
        za = session.scalar(select(CloudflareZone).where(CloudflareZone.zone == "aaa.com"))
        zb = session.scalar(select(CloudflareZone).where(CloudflareZone.zone == "bbb.com"))
        assert za.cf_account_id == a["id"]
        assert zb.cf_account_id == b["id"]
        acc_a = session.get(CloudflareAccount, a["id"])
        acc_b = session.get(CloudflareAccount, b["id"])
        assert acc_a.zone_count == 1
        assert acc_b.zone_count == 1

    # The create-zone POSTs used each account's own Bearer token.
    post_auths = {auth for (auth, method, path) in cf.auth_calls if method == "POST" and path == "/zones"}
    assert "Bearer tok-a" in post_auths
    assert "Bearer tok-b" in post_auths


def test_zone_disable_decrements_pool_count(cf, fake_pdns):
    a = _add(name="a", token="tok-a", account_id="cf-a")
    _managed_zone("solo.com", fake_pdns)
    cloudflare_ops.zone_enable({"domain": "solo.com"})
    with write_session() as session:
        assert session.get(CloudflareAccount, a["id"]).zone_count == 1
    cloudflare_ops.zone_disable({"domain": "solo.com"})
    with write_session() as session:
        assert session.get(CloudflareAccount, a["id"]).zone_count == 0


def test_zone_enable_refuses_when_pool_full(cf, fake_pdns):
    _add(name="a", max_zones=1)
    with write_session() as session:
        session.scalar(select(CloudflareAccount).where(CloudflareAccount.name == "a")).zone_count = 1
    _managed_zone("nope.com", fake_pdns)
    with pytest.raises(RuntimeError, match="capacity"):
        cloudflare_ops.zone_enable({"domain": "nope.com"})
